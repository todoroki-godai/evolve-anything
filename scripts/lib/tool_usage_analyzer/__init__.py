"""ツール利用分析モジュール。

セッション JSONL からツール呼び出しを抽出・分類し、
discover / audit 向けの分析結果を提供する。
"""
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
GLOBAL_RULES_DIR = Path.home() / ".claude" / "rules"
GLOBAL_HOOKS_DIR = Path.home() / ".claude" / "hooks"
RL_HOOKS_DIR = Path.home() / ".claude" / "evolve-anything" / "hooks"

REPEATING_THRESHOLD = 5

# evolve Step 10.2 閾値定数
BUILTIN_THRESHOLD = 10
SLEEP_THRESHOLD = 20
BASH_RATIO_THRESHOLD = 0.40
COMPLIANCE_GOOD_THRESHOLD = 0.90

# ── Stall Recovery 検出定数 ──────────────────────────

LONG_COMMAND_PATTERNS = [
    r"\bcdk\s+deploy\b",
    r"\bdocker\s+build\b",
    r"\bnpm\s+install\b",
    r"\byarn\s+install\b",
    r"\bpip\s+install\b",
    r"\bpip3\s+install\b",
    r"\bcargo\s+build\b",
    r"\bmake\b",
    r"\bgradle\b",
    r"\bmvn\b",
    r"\bterraform\s+apply\b",
]

INVESTIGATION_COMMANDS = {"pgrep", "ps", "lsof", "fuser", "top", "htop"}

RECOVERY_COMMANDS = {"kill", "pkill", "killall"}

STALL_RECOVERY_MIN_SESSIONS = 2
STALL_RECOVERY_RECENCY_DAYS = 30

# Built-in 代替可能コマンド → 推奨ツール
BUILTIN_REPLACEABLE_MAP = {
    "cat": "Read",
    "grep": "Grep",
    "rg": "Grep",
    "find": "Glob",
    "head": "Read",
    "tail": "Read",
    "wc": "Read",
    "sed": "Edit",
    "awk": "Edit",
}

# コマンド+オプションの除外パターン（Built-in では代替不可）
LEGITIMATE_COMMAND_PATTERNS = {
    ("tail", "-f"),
    ("tail", "-F"),
    ("find", "-exec"),
    ("find", "-delete"),
    ("sed", "-i"),
}


# Phase 6 / Slice 1: session_io / stall を別モジュールへ分離（後方互換 re-export）
from .session_io import (  # noqa: E402,F401
    _resolve_session_dir,
    extract_tool_calls,
    extract_tool_calls_by_session,
)
from .stall import (  # noqa: E402,F401
    _classify_stall_step,
    _detect_stall_in_session,
    detect_stall_recovery_patterns,
    stall_pattern_to_pitfall_candidate,
)


# Phase 6 / Slice 2: Bash コマンド分類を classify.py に分離（後方互換 re-export）
from .classify import (  # noqa: E402,F401
    _classify_subcategory,
    _get_command_head,
    _get_command_key,
    _is_cat_replaceable,
    classify_bash_commands,
    detect_repeating_commands,
)


# Phase 6 / Slice 3: rule/hook 生成 + 導入確認を分離（後方互換 re-export）
from .codegen import (  # noqa: E402,F401
    _HOOK_TEMPLATE,
    generate_hook_template,
    generate_rule_candidates,
)
from .install_check import (  # noqa: E402,F401
    check_artifact_installed,
    check_hook_installed,
)


def analyze_tool_usage(
    project_root: Optional[Path] = None,
    threshold: int = REPEATING_THRESHOLD,
    *,
    projects_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """ツール利用分析を一括実行する。

    Returns:
        discover 向けの結果辞書
    """
    tool_counts, bash_commands = extract_tool_calls(
        project_root, projects_dir=projects_dir,
    )

    if not tool_counts:
        return {
            "builtin_replaceable": [],
            "repeating_patterns": [],
            "cli_summary": {},
            "total_tool_calls": 0,
            "bash_calls": 0,
            "bash_ratio": 0.0,
        }

    classified = classify_bash_commands(bash_commands)
    repeating = detect_repeating_commands(bash_commands, threshold=threshold)

    # builtin_replaceable をサマリ化
    replaceable_summary: Counter = Counter()
    for item in classified["builtin_replaceable"]:
        replaceable_summary[f"{item['head']} → {item['alternative']}"] += 1

    builtin_replaceable = [
        {"pattern": pattern, "count": count}
        for pattern, count in replaceable_summary.most_common()
    ]

    # CLI サマリ
    cli_counter: Counter = Counter()
    for item in classified["cli_legitimate"]:
        cli_counter[item["head"]] += 1

    # rule / hook 候補の生成
    rule_candidates = generate_rule_candidates(builtin_replaceable)
    hook_candidate = generate_hook_template(builtin_replaceable)

    # 導入状態の確認
    hook_status = check_hook_installed()

    total_calls = sum(tool_counts.values())
    bash_calls = tool_counts.get("Bash", 0)
    result: Dict[str, Any] = {
        "builtin_replaceable": builtin_replaceable,
        "repeating_patterns": repeating,
        "cli_summary": dict(cli_counter.most_common(10)),
        "total_tool_calls": total_calls,
        "bash_calls": bash_calls,
        "bash_ratio": bash_calls / total_calls if total_calls > 0 else 0.0,
        "hook_status": hook_status,
    }

    if rule_candidates:
        result["rule_candidates"] = rule_candidates
    if hook_candidate:
        result["hook_candidate"] = hook_candidate

    return result
