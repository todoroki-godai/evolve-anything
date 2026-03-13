"""検証知見カタログ — 汎用的な検証ルールのテンプレート管理。

verification_catalog（定義）→ discover（検出）→ evolve（変換）→ remediation（修正）
の4層で共有する。
"""
import logging
import re
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 閾値定数 ──────────────────────────────────────────
DATA_CONTRACT_MIN_PATTERNS = 3
DETECTION_TIMEOUT_SECONDS = 5
MAX_CATALOG_ENTRIES = 10
LARGE_REPO_FILE_THRESHOLD = 1000

# ── 除外ディレクトリ ──────────────────────────────────
EXCLUDE_DIRS = {"node_modules", ".venv", "__pycache__", ".git", ".tox", "dist", "build"}
PRIORITY_DIRS = ["scripts", "src", "lib", "skills"]

# ── ルールテンプレート ────────────────────────────────
_PYTHON_RULE_TEMPLATE = """# データ変換コードの契約確認
モジュール間のデータ変換・統合コードを書く前に、ソース関数の返り値構造（dictキー・型）を Read で確認する。自作テストデータは自作の誤りを検出できないため、既存テストの fixture も参照する。
"""

_TYPESCRIPT_RULE_TEMPLATE = """# データ変換コードの契約確認
モジュール間のデータ変換・統合コードを書く前に、ソース関数の戻り型（interface/type）を Read で確認する。自作テストデータは自作の誤りを検出できないため、既存テストの fixture も参照する。
"""

# ── カタログ定義 ──────────────────────────────────────
VERIFICATION_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "data-contract-verification",
        "type": "rule",
        "description": "モジュール間データ変換コード記述前にソース関数の返り値構造を確認する",
        "rule_template": _PYTHON_RULE_TEMPLATE.strip(),
        "rule_filename": "verify-data-contract.md",
        "detection_fn": "detect_data_contract_verification",
        "applicability": "conditional",
    },
]


def _safe_result(error_msg: str = "") -> Dict[str, Any]:
    """エラー/タイムアウト時の安全な返り値。"""
    if error_msg:
        logger.warning("verification detection error: %s", error_msg)
    return {"applicable": False, "evidence": [], "confidence": 0.0}


def _detect_primary_language(project_dir: Path) -> str:
    """プロジェクトの主要言語を .py vs .ts/.tsx ファイル数で判定する。同数時は Python。"""
    py_count = 0
    ts_count = 0
    for f in _iter_source_files(project_dir):
        if f.suffix == ".py":
            py_count += 1
        elif f.suffix in (".ts", ".tsx"):
            ts_count += 1
    return "typescript" if ts_count > py_count else "python"


def _iter_source_files(project_dir: Path):
    """走査対象ファイルを yield する。大規模リポジトリでは優先ディレクトリに限定。"""
    # ファイル数チェック（簡易）
    all_files = []
    try:
        for p in project_dir.rglob("*"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if p.is_file() and p.suffix in (".py", ".ts", ".tsx"):
                all_files.append(p)
                if len(all_files) > LARGE_REPO_FILE_THRESHOLD:
                    break
    except (PermissionError, OSError):
        pass

    if len(all_files) > LARGE_REPO_FILE_THRESHOLD:
        # 大規模リポジトリ → 優先ディレクトリのみ
        for d in PRIORITY_DIRS:
            dir_path = project_dir / d
            if not dir_path.is_dir():
                continue
            try:
                for p in dir_path.rglob("*"):
                    if any(part in EXCLUDE_DIRS for part in p.parts):
                        continue
                    if p.is_file() and p.suffix in (".py", ".ts", ".tsx"):
                        yield p
            except (PermissionError, OSError):
                continue
    else:
        yield from all_files


# ── 検出パターン ─────────────────────────────────────

# Python: from X import Y + dict 構築パターン
_PY_IMPORT_RE = re.compile(r"^from\s+\w+(?:\.\w+)*\s+import\s+", re.MULTILINE)
_PY_DICT_BUILD_RE = re.compile(
    r'(?:\w+\s*=\s*\{[^}]*:[^}]*\}|\.append\(\s*\{|result\[|data\[)',
    re.MULTILINE,
)

# TypeScript: import { X } from "Y" + オブジェクトリテラル
_TS_IMPORT_RE = re.compile(r'^import\s+\{[^}]+\}\s+from\s+["\']', re.MULTILINE)
_TS_OBJ_BUILD_RE = re.compile(
    r'(?:const\s+\w+\s*[:=]\s*\{|\w+\s*=\s*\{[^}]*:[^}]*\})',
    re.MULTILINE,
)


def _has_cross_module_pattern(filepath: Path) -> bool:
    """ファイルがモジュール間変換パターンを持つか判定する。"""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except (PermissionError, OSError):
        return False

    if filepath.suffix == ".py":
        return bool(_PY_IMPORT_RE.search(content) and _PY_DICT_BUILD_RE.search(content))
    elif filepath.suffix in (".ts", ".tsx"):
        return bool(_TS_IMPORT_RE.search(content) and _TS_OBJ_BUILD_RE.search(content))
    return False


def detect_data_contract_verification(project_dir: Path) -> Dict[str, Any]:
    """data-contract-verification の検出関数。

    プロジェクト内の Python/TypeScript ファイルを走査し、
    モジュール間 dict 変換パターンを検出する。
    """
    if not project_dir.is_dir():
        return _safe_result("project_dir does not exist")

    evidence: List[str] = []
    try:
        for filepath in _iter_source_files(project_dir):
            if _has_cross_module_pattern(filepath):
                try:
                    rel = str(filepath.relative_to(project_dir))
                except ValueError:
                    rel = str(filepath)
                evidence.append(rel)
                if len(evidence) >= 10:  # evidence 最大10件
                    break
    except Exception as e:
        return _safe_result(str(e))

    count = len(evidence)
    if count >= DATA_CONTRACT_MIN_PATTERNS:
        confidence = min(0.7, 0.5 + count * 0.04)  # regex のみなので 0.7 上限
        return {
            "applicable": True,
            "evidence": evidence,
            "confidence": confidence,
            "llm_escalation_prompt": (
                f"以下のプロジェクトで {count} 箇所のモジュール間データ変換パターンが検出されました。"
                f"このプロジェクトに「データ変換コード記述前にソース関数の返り値構造を確認する」ルールは有用ですか？"
                f"yes/no で回答してください。\n\n検出ファイル:\n" + "\n".join(evidence[:5])
            ),
        }
    return {
        "applicable": False,
        "evidence": evidence,
        "confidence": 0.0,
    }


# ── 検出関数ディスパッチ ─────────────────────────────

_DETECTION_FN_DISPATCH: Dict[str, Any] = {
    "detect_data_contract_verification": detect_data_contract_verification,
}


def _run_detection_fn(fn_name: str, project_dir: Path) -> Dict[str, Any]:
    """検出関数をタイムアウト付きで実行する。"""
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


# ── 公開 API ─────────────────────────────────────────

def check_verification_installed(entry: Dict[str, Any], project_dir: Path) -> bool:
    """対象プロジェクトにエントリのルールが導入済みかチェックする。"""
    rule_filename = entry.get("rule_filename", "")
    if not rule_filename:
        return False
    rule_path = project_dir / ".claude" / "rules" / rule_filename
    return rule_path.exists()


def get_rule_template(entry: Dict[str, Any], project_dir: Path) -> str:
    """プロジェクトの主要言語に応じたルールテンプレートを返す。"""
    if entry["id"] == "data-contract-verification":
        lang = _detect_primary_language(project_dir)
        if lang == "typescript":
            return _TYPESCRIPT_RULE_TEMPLATE.strip()
    return entry["rule_template"]


def detect_verification_needs(project_dir: Path) -> List[Dict[str, Any]]:
    """VERIFICATION_CATALOG を走査し、未導入 + 適用可能なエントリをリストとして返す。"""
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
