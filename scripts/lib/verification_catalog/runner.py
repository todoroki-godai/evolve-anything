"""verification_catalog のディスパッチ + 公開 API ランナー。

`_DETECTION_FN_DISPATCH` (検出関数ディスパッチ) /
`_run_detection_fn` (タイムアウト付き実行) /
`_CONTENT_KEYWORDS_MAP` (content-aware ルール導入判定) /
`check_verification_installed` / `get_rule_template` / `detect_verification_needs`
を提供する。
"""
import signal
from pathlib import Path
from typing import Any, Dict, List

from .detectors_advanced import (
    detect_cross_layer_consistency,
    detect_happy_path_test_gap,
)
from .detectors_basic import (
    detect_data_contract_verification,
    detect_evidence_verification,
    detect_side_effect_verification,
)
from .helpers import _detect_primary_language, _safe_result
from .templates import _TYPESCRIPT_RULE_TEMPLATE


# ── 検出関数ディスパッチ ─────────────────────────────

_DETECTION_FN_DISPATCH: Dict[str, Any] = {
    "detect_data_contract_verification": detect_data_contract_verification,
    "detect_side_effect_verification": detect_side_effect_verification,
    "detect_evidence_verification": detect_evidence_verification,
    "detect_happy_path_test_gap": detect_happy_path_test_gap,
    "detect_cross_layer_consistency": detect_cross_layer_consistency,
}


def _run_detection_fn(fn_name: str, project_dir: Path) -> Dict[str, Any]:
    """検出関数をタイムアウト付きで実行する。"""
    from . import DETECTION_TIMEOUT_SECONDS

    fn = _DETECTION_FN_DISPATCH.get(fn_name)
    if fn is None:
        return _safe_result(f"unknown detection_fn: {fn_name}")

    # タイムアウト制御（Unix のみ signal.alarm、非対応環境はフォールバック）
    try:
        def _timeout_handler(signum, frame):
            raise TimeoutError("detection timeout")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(DETECTION_TIMEOUT_SECONDS)
        try:
            result = fn(project_dir)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        return result
    except TimeoutError:
        return _safe_result(f"detection_fn {fn_name} timed out ({DETECTION_TIMEOUT_SECONDS}s)")
    except Exception as e:
        return _safe_result(f"detection_fn {fn_name} error: {e}")


# ── content-aware ルール導入判定 ──────────────────────

_SIDE_EFFECT_CONTENT_KEYWORDS = ["副作用", "side effect"]
_EVIDENCE_CONTENT_KEYWORDS = ["証拠", "evidence", "verify-before", "証拠提示", "before claim"]
_CROSS_LAYER_CONTENT_KEYWORDS = ["cross-layer", "IaC", "クロスレイヤー", "環境変数", "IAM"]
_HAPPY_PATH_CONTENT_KEYWORDS = ["ハッピーパス", "happy path", "E2Eテスト", "正常系テスト"]

# エントリ ID → content-aware キーワードのマッピング
_CONTENT_KEYWORDS_MAP: Dict[str, List[str]] = {
    "side-effect-verification": _SIDE_EFFECT_CONTENT_KEYWORDS,
    "evidence-before-claims": _EVIDENCE_CONTENT_KEYWORDS,
    "happy-path-test-verification": _HAPPY_PATH_CONTENT_KEYWORDS,
    "cross-layer-consistency": _CROSS_LAYER_CONTENT_KEYWORDS,
}


def check_verification_installed(entry: Dict[str, Any], project_dir: Path) -> bool:
    """対象プロジェクトにエントリのルールが導入済みかチェックする。

    1. rule_filename のファイルが存在するか
    2. content-aware: 既存ルールファイルにキーワードが含まれるか
    """
    rule_filename = entry.get("rule_filename", "")
    if not rule_filename:
        return False
    rules_dir = project_dir / ".claude" / "rules"
    rule_path = rules_dir / rule_filename
    if rule_path.exists():
        return True

    # content-aware チェック
    entry_id = entry.get("id", "")
    keywords = _CONTENT_KEYWORDS_MAP.get(entry_id)
    if keywords and rules_dir.is_dir():
        for rule_file in rules_dir.glob("*.md"):
            if rule_file.name == rule_filename:
                continue
            try:
                content = rule_file.read_text(encoding="utf-8", errors="ignore")
                if any(kw in content for kw in keywords):
                    return True
            except (PermissionError, OSError):
                continue

    return False


def get_rule_template(entry: Dict[str, Any], project_dir: Path) -> str:
    """プロジェクトの主要言語に応じたルールテンプレートを返す。"""
    if entry["id"] == "data-contract-verification":
        lang = _detect_primary_language(project_dir)
        if lang == "typescript":
            return _TYPESCRIPT_RULE_TEMPLATE.strip()
    return entry["rule_template"]


def detect_verification_needs(project_dir: Path) -> List[Dict[str, Any]]:
    """VERIFICATION_CATALOG を走査し、未導入 + 適用可能なエントリをリストとして返す。"""
    from . import VERIFICATION_CATALOG

    if not project_dir.is_dir():
        return []

    needs: List[Dict[str, Any]] = []
    for entry in VERIFICATION_CATALOG:
        if check_verification_installed(entry, project_dir):
            continue

        applicability = entry.get("applicability", "always")
        if applicability == "always":
            needs.append({
                **entry,
                "detection_result": {"applicable": True, "evidence": [], "confidence": 1.0},
            })
        elif applicability == "conditional":
            fn_name = entry.get("detection_fn")
            if fn_name:
                detection_result = _run_detection_fn(fn_name, project_dir)
                if detection_result.get("applicable"):
                    needs.append({
                        **entry,
                        "detection_result": detection_result,
                    })

    return needs
