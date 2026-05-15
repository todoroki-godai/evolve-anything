"""ツール利用分析モジュール。

セッション JSONL からツール呼び出しを抽出・分類し、
discover / audit 向けの分析結果を提供する。
"""
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
GLOBAL_RULES_DIR = Path.home() / ".claude" / "rules"
GLOBAL_HOOKS_DIR = Path.home() / ".claude" / "hooks"
RL_HOOKS_DIR = Path.home() / ".claude" / "rl-anything" / "hooks"

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


# ---------- rule / hook 候補生成 ----------


def generate_rule_candidates(
    builtin_replaceable: List[Dict[str, Any]],
    *,
    rules_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """builtin_replaceable 検出結果から global rule 候補を生成する。

    既存ルールとの重複は除外。3行以内制約。

    Returns:
        [{"filename": str, "content": str, "target_commands": [str],
          "alternative_tools": [str], "total_count": int}]
    """
    if rules_dir is None:
        rules_dir = GLOBAL_RULES_DIR

    if not builtin_replaceable:
        return []

    # 既存ルールのファイル名を取得
    existing_rules: set = set()
    if rules_dir.is_dir():
        existing_rules = {f.name for f in rules_dir.glob("*.md")}

    # パターンを代替ツール別にグルーピング
    # "grep → Grep" → tool="Grep", command="grep"
    tool_groups: Dict[str, Dict[str, Any]] = {}  # alternative -> {commands, count}
    for item in builtin_replaceable:
        pattern = item.get("pattern", "")
        count = item.get("count", 0)
        parts = pattern.split(" → ")
        if len(parts) != 2:
            continue
        cmd, alt = parts[0].strip(), parts[1].strip()
        if alt not in tool_groups:
            tool_groups[alt] = {"commands": [], "count": 0}
        tool_groups[alt]["commands"].append(cmd)
        tool_groups[alt]["count"] += count

    # 候補の rule ファイル名が既に存在するかチェック
    filename = "avoid-bash-builtin.md"
    if filename in existing_rules:
        return []

    if not tool_groups:
        return []

    # 全コマンドをまとめた1つの rule を生成
    all_commands = []
    all_alternatives = []
    total_count = 0
    for alt, group in sorted(tool_groups.items(), key=lambda x: -x[1]["count"]):
        all_commands.extend(group["commands"])
        if alt not in all_alternatives:
            all_alternatives.append(alt)
        total_count += group["count"]

    # コマンド→代替のマッピング文字列を生成
    mapping_parts = []
    for alt, group in sorted(tool_groups.items(), key=lambda x: -x[1]["count"]):
        cmds = "/".join(sorted(set(group["commands"])))
        mapping_parts.append(f"{cmds} は {alt}")

    content = (
        "# Bash Built-in 代替コマンド禁止\n"
        f"{', '.join(mapping_parts)} を使用する。\n"
        "パイプやリダイレクトを伴う複合コマンドは Bash で OK。\n"
    )

    return [{
        "filename": filename,
        "content": content,
        "target_commands": sorted(set(all_commands)),
        "alternative_tools": all_alternatives,
        "total_count": total_count,
    }]


_HOOK_TEMPLATE = '''\
#!/usr/bin/env python3
"""PreToolUse hook: Bash で Built-in ツール代替可能なコマンドを検出して block する。"""
import json
import sys

REPLACEABLE = {replaceable_map}

LEGITIMATE_MARKERS = {{"<<", ">>", "|"}}
LEGITIMATE_PATTERNS = {legitimate_patterns}


def _get_command_head(command):
    parts = command.strip().split()
    idx = 0
    while idx < len(parts) and parts[idx] in ("env", "sudo"):
        idx += 1
    return parts[idx] if idx < len(parts) else ""


def check_command(command):
    head = _get_command_head(command)
    if head not in REPLACEABLE:
        return None
    for marker in LEGITIMATE_MARKERS:
        if marker in command:
            return None
    for cmd, opt in LEGITIMATE_PATTERNS:
        if head == cmd and opt in command:
            return None
    alternative = REPLACEABLE[head]
    return f"`{{head}}` の代わりに {{alternative}} ツールを使用してください。Built-in ツールの方がユーザー体験が良く、権限管理も適切です。"


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    command = data.get("tool_input", {{}}).get("command", "")
    if not command:
        sys.exit(0)
    reason = check_command(command)
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
'''


def generate_hook_template(
    builtin_replaceable: List[Dict[str, Any]],
    *,
    output_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """builtin_replaceable パターンから PreToolUse hook スクリプトを生成する。

    Returns:
        {"script_path": str, "script_content": str,
         "settings_diff": str, "target_commands": [str]} or None
    """
    if output_dir is None:
        output_dir = GLOBAL_HOOKS_DIR

    if not builtin_replaceable:
        return None

    # 検出されたコマンドから REPLACEABLE map を構築
    replaceable: Dict[str, str] = {}
    for item in builtin_replaceable:
        pattern = item.get("pattern", "")
        parts = pattern.split(" → ")
        if len(parts) != 2:
            continue
        cmd, alt = parts[0].strip(), parts[1].strip()
        replaceable[cmd] = alt

    if not replaceable:
        return None

    # スクリプト生成
    replaceable_repr = json.dumps(replaceable, ensure_ascii=False, indent=4)
    legitimate_repr = repr(LEGITIMATE_COMMAND_PATTERNS)
    script_content = _HOOK_TEMPLATE.format(
        replaceable_map=replaceable_repr,
        legitimate_patterns=legitimate_repr,
    )

    script_path = output_dir / "check-bash-builtin.py"

    # settings.json の差分案
    settings_diff = json.dumps({
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": f"python3 {script_path}",
                }],
            }],
        },
    }, ensure_ascii=False, indent=2)

    return {
        "script_path": str(script_path),
        "script_content": script_content,
        "settings_diff": settings_diff,
        "target_commands": sorted(replaceable.keys()),
    }


def check_artifact_installed(
    artifact: Dict[str, Any],
    *,
    hooks_dir: Optional[Path] = None,
    rules_dir: Optional[Path] = None,
    settings_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """推奨 artifact の導入状態を確認する。

    Returns:
        {"installed": bool, "artifacts_found": list[str],
         "content_matched": bool | None}
    """
    artifacts_found: List[str] = []
    content_matched: Optional[bool] = None

    # hook_path チェック
    hook_path = artifact.get("hook_path")
    if hook_path:
        try:
            if hook_path.exists():
                artifacts_found.append("hook")
        except OSError:
            pass

    # rule path チェック
    rule_path = artifact.get("path")
    if rule_path:
        try:
            if rule_path.exists():
                artifacts_found.append("rule")
        except OSError:
            pass

    # content_patterns チェック
    content_patterns = artifact.get("content_patterns")
    if content_patterns and hook_path:
        try:
            if hook_path.exists():
                import re
                hook_content = hook_path.read_text(encoding="utf-8")
                all_matched = all(
                    re.search(pattern, hook_content)
                    for pattern in content_patterns
                )
                content_matched = all_matched
            else:
                content_matched = False
        except OSError:
            content_matched = None

    # installed 判定: 必要な artifact が全て存在 + content_pattern マッチ
    rule_ok = rule_path is None or "rule" in artifacts_found
    hook_ok = hook_path is None or "hook" in artifacts_found
    content_ok = content_matched is not False if content_patterns else True
    installed = rule_ok and hook_ok and content_ok

    return {
        "installed": installed,
        "artifacts_found": artifacts_found,
        "content_matched": content_matched,
    }


def check_hook_installed(
    *,
    hook_path: Optional[Path] = None,
    settings_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """check-bash-builtin hook の導入状態を確認する。

    Returns:
        {"installed": bool, "hook_exists": bool, "settings_registered": bool}
    """
    if hook_path is None:
        hook_path = GLOBAL_HOOKS_DIR / "check-bash-builtin.py"
    if settings_path is None:
        settings_path = Path.home() / ".claude" / "settings.json"

    hook_exists = hook_path.exists()

    settings_registered = False
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            for hook_group in settings.get("hooks", {}).get("PreToolUse", []):
                for hook in hook_group.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "check-bash-builtin" in cmd:
                        settings_registered = True
                        break
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "installed": hook_exists and settings_registered,
        "hook_exists": hook_exists,
        "settings_registered": settings_registered,
    }


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

    result: Dict[str, Any] = {
        "builtin_replaceable": builtin_replaceable,
        "repeating_patterns": repeating,
        "cli_summary": dict(cli_counter.most_common(10)),
        "total_tool_calls": sum(tool_counts.values()),
        "bash_calls": tool_counts.get("Bash", 0),
        "hook_status": hook_status,
    }

    if rule_candidates:
        result["rule_candidates"] = rule_candidates
    if hook_candidate:
        result["hook_candidate"] = hook_candidate

    return result
