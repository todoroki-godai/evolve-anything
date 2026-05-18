#!/usr/bin/env python3
"""PostToolUse hook — Edit/Write で .claude/memory/*.md の update_count を自動インクリメント。

arXiv:2605.12978 の「LLM 自己更新メモリの劣化」対策 (Issue #151)。
SKILL.md Step 7.6 の LLM 手動インクリメントに頼らず、hook 層で強制する。
LLM 呼び出しは行わない（MUST NOT）。
セッションをブロックしない（サイレント失敗）。
"""
import json
import sys
from pathlib import Path

_scripts_lib = str(Path(__file__).resolve().parent.parent / "scripts" / "lib")
if _scripts_lib not in sys.path:
    sys.path.insert(0, _scripts_lib)

from frontmatter import update_frontmatter
from memory_temporal import parse_memory_temporal

MEMORY_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})


def is_memory_file(path: str) -> bool:
    """パスが .claude/*/memory/*.md かどうか判定する。

    MEMORY.md（インデックスファイル）は除外する。MEMORY.md はポインタ index で
    LLM 合成コンテンツではないため、degradation guard の対象外。
    """
    if not path:
        return False
    p = Path(path)
    if p.suffix != ".md":
        return False
    if p.name == "MEMORY.md":
        return False
    if p.parent.name != "memory":
        return False
    return any(part == ".claude" for part in p.parts)


def handle_event(event: dict) -> None:
    """PostToolUse イベントを処理して update_count をインクリメントする。"""
    if event.get("tool_name") not in MEMORY_TOOLS:
        return

    # ツール呼び出しが失敗した場合はスキップ（ファイルは変更されていない）
    tool_result = event.get("tool_result") or {}
    if isinstance(tool_result, dict) and tool_result.get("is_error"):
        return

    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return

    file_path = tool_input.get("file_path", "")
    if not file_path or not is_memory_file(file_path):
        return

    path = Path(file_path)
    if not path.exists():
        return

    try:
        temporal = parse_memory_temporal(path)
        current = temporal.get("update_count", 0)
        update_frontmatter(path, {"update_count": current + 1})
    except Exception:
        pass  # サイレント失敗


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_event(event)
    except (json.JSONDecodeError, OSError):
        pass


if __name__ == "__main__":
    main()
