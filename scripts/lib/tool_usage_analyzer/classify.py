"""Bash コマンド分類モジュール (Phase 6 / Slice 2)。

`classify_bash_commands` / `detect_repeating_commands` および
それらが依存する小ヘルパ群（`_is_cat_replaceable` / `_get_command_head` /
`_get_command_key` / `_classify_subcategory`）を提供する。
"""
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List

# examples フィールドの truncate (#555)
from rule_violation_lane import truncate_example  # noqa: E402

# `VAR=value` 形式の代入プレフィックス（env/sudo 同様、実コマンドの前置として読み飛ばす）
_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _skip_command_prefixes(parts: List[str]) -> int:
    """env / sudo / `VAR=value` 代入プレフィックスを読み飛ばし、実コマンド開始 index を返す。"""
    idx = 0
    while idx < len(parts) and (
        parts[idx] in ("env", "sudo") or _VAR_ASSIGN_RE.match(parts[idx])
    ):
        idx += 1
    return idx


def _is_cat_replaceable(command: str) -> bool:
    """cat コマンドが Read 代替可能かどうかを判定する。

    heredoc (<<) やリダイレクト出力 (>, >>) がある場合は除外。
    """
    if "<<" in command:
        return False
    # リダイレクト出力の検出（> だが >> も含む、ただしパイプ後の > は含む）
    # シンプルに: コマンド文字列に > が含まれ、それが stderr redirect (2>) でもパイプ後でもない場合
    # → design の方針: シンプルな文字列マッチで十分
    for i, ch in enumerate(command):
        if ch == ">" and i > 0 and command[i - 1] != "|" and command[i - 1] != "2":
            return False
        if ch == ">" and i == 0:
            return False
    return True


def _get_command_head(command: str) -> str:
    """コマンド文字列から先頭語を取得する。"""
    parts = command.strip().split()
    if not parts:
        return ""
    # env / sudo / VAR=value 代入プレフィックスをスキップ
    idx = _skip_command_prefixes(parts)
    return parts[idx] if idx < len(parts) else ""


def classify_bash_commands(
    commands: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Bash コマンドを3カテゴリに分類する。

    Returns:
        {
            "builtin_replaceable": [{"command": ..., "head": ..., "alternative": ...}],
            "repeating_pattern": [],  # detect_repeating_commands で後処理
            "cli_legitimate": [{"command": ..., "head": ...}],
        }
    """
    from . import BUILTIN_REPLACEABLE_MAP

    result: Dict[str, List[Dict[str, Any]]] = {
        "builtin_replaceable": [],
        "repeating_pattern": [],
        "cli_legitimate": [],
    }

    for cmd in commands:
        head = _get_command_head(cmd)
        if not head:
            continue

        if head in BUILTIN_REPLACEABLE_MAP:
            # cat の特殊ルール
            if head == "cat" and not _is_cat_replaceable(cmd):
                result["cli_legitimate"].append({"command": cmd, "head": head})
            else:
                result["builtin_replaceable"].append({
                    "command": cmd,
                    "head": head,
                    "alternative": BUILTIN_REPLACEABLE_MAP[head],
                })
        else:
            result["cli_legitimate"].append({"command": cmd, "head": head})

    return result


def _get_command_key(command: str) -> str:
    """コマンドの「先頭語 + サブコマンド」キーを生成する。"""
    parts = command.strip().split()
    if not parts:
        return ""

    idx = _skip_command_prefixes(parts)

    if idx >= len(parts):
        return ""

    head = parts[idx]

    # サブコマンドがあれば2語目も取得
    if idx + 1 < len(parts):
        sub = parts[idx + 1]
        # オプション（-で始まる）はスキップ
        if not sub.startswith("-"):
            return f"{head} {sub}"

    return head


def detect_repeating_commands(
    commands: List[str],
    threshold: int = 5,
) -> List[Dict[str, Any]]:
    """繰り返しパターンを検出する。

    先頭語+サブコマンドでグルーピングし、閾値以上のパターンを返す。
    """
    key_counter: Counter = Counter()
    key_examples: Dict[str, List[str]] = defaultdict(list)

    for cmd in commands:
        key = _get_command_key(cmd)
        if not key:
            continue
        key_counter[key] += 1
        if len(key_examples[key]) < 3:
            key_examples[key].append(cmd)

    patterns = []
    for key, count in key_counter.most_common():
        if count >= threshold:
            head = key.split()[0]
            # サブカテゴリ分類
            subcategory = _classify_subcategory(head, key)
            # examples を 1行・120字に truncate (#555)
            truncated = [truncate_example(ex) for ex in key_examples[key]]
            patterns.append({
                "pattern": key,
                "count": count,
                "subcategory": subcategory,
                "examples": truncated,
            })

    return patterns


def _classify_subcategory(head: str, key: str) -> str:
    """先頭語からサブカテゴリを推定する。"""
    if head == "python3":
        if "pytest" in key:
            return "pytest"
        return "script"
    if head == "git":
        return "vcs"
    if head == "gh":
        return "github"
    if head in ("npm", "npx", "yarn", "pnpm", "bun"):
        return "package_manager"
    if head in ("pip", "pip3", "uv"):
        return "python_package"
    if head in ("docker", "docker-compose"):
        return "container"
    if head in ("aws", "cdk"):
        return "cloud"
    return "cli"
