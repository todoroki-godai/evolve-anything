"""エラーパターン / 繰り返し correction / rejection 検出 + scope 判定。

discover/__init__.py から re-export される（後方互換）。
DATA_DIR / HISTORY_DIR / 閾値定数は package 経由で遅延参照する
（テストの patch / DATA_DIR 差し替えに追従）。
"""
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from .suppression import load_jsonl, load_suppression_list


HOOK_CANDIDATE_THRESHOLD = 3


def detect_error_patterns(
    threshold: int = 3,
    *,
    project_root: Optional[Path] = None,
    include_unknown: bool = False,
) -> List[Dict[str, Any]]:
    """繰り返しエラーパターンの検出（errors、3+閾値）。"""
    from . import DATA_DIR
    from telemetry_query import query_errors

    project_name = project_root.name if project_root else None
    errors = query_errors(
        project=project_name,
        include_unknown=include_unknown,
        errors_file=DATA_DIR / "errors.jsonl",
    )
    counter: Counter = Counter()
    for rec in errors:
        error = rec.get("error", "")[:200]
        if error:
            counter[error] += 1

    suppressed = load_suppression_list()
    patterns = []
    for error, count in counter.most_common():
        if count >= threshold and error not in suppressed:
            patterns.append({
                "type": "error",
                "pattern": error,
                "count": count,
                "suggestion": "rule_candidate",
            })
    return patterns


def detect_repeated_correction_patterns(
    corrections: List[Dict[str, Any]],
    threshold: int = HOOK_CANDIDATE_THRESHOLD,
) -> List[Dict[str, Any]]:
    """同じ corrections パターンが N 回繰り返されたものを hook 候補として返す (#41)。

    ルールで防げない繰り返しパターンを検出し、PreToolUse/PostToolUse hook 候補として提案する。
    """
    counter: Counter = Counter()
    sample: Dict[str, str] = {}

    for rec in corrections:
        msg = (rec.get("message") or "").strip()
        if not msg:
            continue
        key = msg[:80]
        counter[key] += 1
        if key not in sample:
            sample[key] = msg

    suppressed = load_suppression_list()
    candidates = []
    for key, count in counter.most_common():
        if count >= threshold and key not in suppressed:
            candidates.append({
                "type": "hook_candidate",
                "pattern": key,
                "full_message": sample[key],
                "count": count,
                "suggestion": "hook_candidate",
                "reason": f"同じパターンが {count} 回繰り返された — ルールより hook での防止を推奨",
            })
    return candidates


def detect_rejection_patterns(threshold: int = 3) -> List[Dict[str, Any]]:
    """繰り返し却下理由の検出（rejection_reason、3+閾値）。"""
    from . import HISTORY_DIR
    history_file = HISTORY_DIR / "history.jsonl"
    records = load_jsonl(history_file)

    counter: Counter = Counter()
    for rec in records:
        reason = rec.get("rejection_reason")
        if reason:
            counter[reason] += 1

    suppressed = load_suppression_list()
    patterns = []
    for reason, count in counter.most_common():
        if count >= threshold and reason not in suppressed:
            patterns.append({
                "type": "rejection",
                "pattern": reason,
                "count": count,
                "suggestion": "rule_candidate",
            })
    return patterns


def determine_scope(pattern: Dict[str, Any]) -> str:
    """スコープ配置の判断（global / project / plugin）。

    カスタム Agent は agent_type フィールドから判定する。
    """
    # カスタム Agent: agent_type フィールドで判定
    agent_type = pattern.get("agent_type")
    if agent_type == "custom_global":
        return "global"
    if agent_type == "custom_project":
        return "project"

    p = pattern.get("pattern", "").lower()

    # global 配置の兆候
    global_keywords = ["git", "commit", "pr", "pull request", "test", "lint", "claude", "format"]
    if any(kw in p for kw in global_keywords):
        return "global"

    # project 配置の兆候
    project_keywords = ["react", "next", "vue", "django", "rails", "prisma", "docker"]
    if any(kw in p for kw in project_keywords):
        return "project"

    return "project"  # デフォルト
