"""rule / hook 候補生成モジュール (Phase 6 / Slice 3)。

`generate_rule_candidates` (global rule 候補生成) と
`generate_hook_template` (PreToolUse hook スクリプト生成) を提供する。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


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
        from . import GLOBAL_RULES_DIR
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
        from . import GLOBAL_HOOKS_DIR
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
    from . import LEGITIMATE_COMMAND_PATTERNS
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
