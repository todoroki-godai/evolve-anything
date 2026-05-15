"""verification_catalog の基本検出関数 3 種。

`detect_data_contract_verification` (モジュール間 dict 変換パターン) /
`detect_side_effect_verification` (DB / MQ / 外部API アクセス) /
`detect_evidence_verification` (corrections.jsonl 内の証拠要求パターン) を提供する。
"""
import re
from pathlib import Path
from typing import Any, Dict, List

from .helpers import (
    _has_cross_module_pattern,
    _is_test_file,
    _iter_source_files,
    _safe_result,
)
from .templates import _SIDE_EFFECT_CATEGORIES

# ── 閾値（__init__.py からも import される SoT は __init__.py 側に置く）──
# Note: 以下は __init__.py から再エクスポートする呼び出し側で使う閾値の参照
# なので、__init__.py のグローバル定数を実行時に lazy lookup する。


def _data_contract_min_patterns() -> int:
    from . import DATA_CONTRACT_MIN_PATTERNS
    return DATA_CONTRACT_MIN_PATTERNS


def _side_effect_min_patterns() -> int:
    from . import SIDE_EFFECT_MIN_PATTERNS
    return SIDE_EFFECT_MIN_PATTERNS


def _evidence_min_patterns() -> int:
    from . import EVIDENCE_MIN_PATTERNS
    return EVIDENCE_MIN_PATTERNS


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
    if count >= _data_contract_min_patterns():
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


def detect_side_effect_verification(project_dir: Path) -> Dict[str, Any]:
    """side-effect-verification の検出関数。

    プロジェクト内の共有リソースアクセスパターン（DB操作・MQ・外部API）を走査し、
    副作用チェックルールの必要性を判定する。
    """
    if not project_dir.is_dir():
        return _safe_result("project_dir does not exist")

    evidence: List[str] = []
    detected_categories: set = set()
    try:
        for filepath in _iter_source_files(project_dir):
            if _is_test_file(filepath):
                continue
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
            except (PermissionError, OSError):
                continue
            for cat_name, pattern in _SIDE_EFFECT_CATEGORIES.items():
                if pattern.search(content):
                    try:
                        rel = str(filepath.relative_to(project_dir))
                    except ValueError:
                        rel = str(filepath)
                    if rel not in evidence:
                        evidence.append(rel)
                    detected_categories.add(cat_name)
            if len(evidence) >= 10:
                break
    except Exception as e:
        return _safe_result(str(e))

    count = len(evidence)
    categories = sorted(detected_categories)
    if count >= _side_effect_min_patterns():
        confidence = min(0.7, 0.5 + count * 0.04)
        cat_str = "・".join({"db": "DB操作", "mq": "メッセージキュー", "api": "外部API"}.get(c, c) for c in categories)
        return {
            "applicable": True,
            "evidence": evidence,
            "detected_categories": categories,
            "confidence": confidence,
            "llm_escalation_prompt": (
                f"以下のプロジェクトで {count} 箇所の共有リソースアクセスパターン（{cat_str}）が検出されました。"
                f"このプロジェクトに「テスト検証時に副作用を確認する」ルールは有用ですか？"
                f"yes/no で回答してください。\n\n検出ファイル:\n" + "\n".join(evidence[:5])
            ),
        }
    return {
        "applicable": False,
        "evidence": evidence,
        "detected_categories": categories,
        "confidence": 0.0,
    }


# ── 証拠提示義務パターン検出 ──────────────────────────

_EVIDENCE_REQUEST_PATTERNS = re.compile(
    r"(?:テスト実行して|テスト(?:を)?(?:走らせ|回し)|確認して|動作確認|"
    r"ビルド通して|コンパイル確認|lint通して|"
    r"run (?:the )?tests?|verify|check (?:it|that)|"
    r"show me (?:the )?(?:output|result)|prove|evidence)",
    re.IGNORECASE,
)


def detect_evidence_verification(project_dir: Path) -> Dict[str, Any]:
    """evidence-before-claims の検出関数。

    corrections.jsonl から「証拠要求」パターンを検出する。
    corrections は内部で telemetry_query 経由で取得する。

    Args:
        project_dir: プロジェクトディレクトリ

    Returns:
        {"applicable": bool, "evidence": [str], "confidence": float}
    """
    if not project_dir.is_dir():
        return _safe_result("project_dir does not exist")

    try:
        import sys as _sys
        # parent.parent: scripts/lib/verification_catalog/ → scripts/lib/
        _lib_dir = str(Path(__file__).resolve().parent.parent)
        if _lib_dir not in _sys.path:
            _sys.path.insert(0, _lib_dir)
        import telemetry_query
        corrections = telemetry_query.query_corrections(
            project=project_dir.name,
        )
    except (ImportError, Exception) as e:
        return _safe_result(f"telemetry_query unavailable: {e}")

    evidence: List[str] = []
    for rec in corrections:
        if not isinstance(rec, dict):
            continue
        message = rec.get("message", "")
        if _EVIDENCE_REQUEST_PATTERNS.search(message):
            evidence.append(message[:120])
            if len(evidence) >= 10:
                break

    count = len(evidence)
    if count >= _evidence_min_patterns():
        confidence = min(0.7, 0.5 + count * 0.04)
        return {
            "applicable": True,
            "evidence": evidence,
            "confidence": confidence,
            "llm_escalation_prompt": (
                f"corrections に {count} 件の証拠要求パターンが検出されました。"
                f"「完了主張の前に検証結果を提示する」ルールは有用ですか？"
                f"yes/no で回答してください。\n\n検出パターン:\n" + "\n".join(evidence[:5])
            ),
        }
    return {
        "applicable": False,
        "evidence": evidence,
        "confidence": 0.0,
    }
