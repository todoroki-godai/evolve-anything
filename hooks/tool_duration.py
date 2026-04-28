#!/usr/bin/env python3
"""PostToolUse hook — Bash ツール実行時間を記録する (CC v2.1.119+)。

duration_ms が slow_threshold_ms (userConfig) 以上の Bash 呼び出しを
tool_durations.jsonl に追記する。slow command パターン検出・performance regression
分析の基盤。LLM 呼び出しは行わない（MUST NOT）。
書き込み失敗時はセッションをブロックしない（サイレント失敗）。

hooks.json の matcher が "Bash" のため、Bash ツール呼び出しのみ受信する。
"""
import json
import os
import sys
from datetime import datetime, timezone

import common

MAX_CMD_LENGTH = 200  # command_preview の文字数上限（表示・ストレージのバランス）


def _get_threshold() -> int:
    """userConfig から slow_threshold_ms を取得する（デフォルト: 1000ms）。"""
    cfg = common.load_user_config()
    return int(cfg.get("slow_threshold_ms", 1000))


def handle_tool_duration(event: dict) -> None:
    """PostToolUse イベントを処理してスロー Bash コマンドを記録する。"""
    duration_ms = event.get("duration_ms")
    if duration_ms is None or not isinstance(duration_ms, (int, float)):
        return
    if duration_ms < _get_threshold():
        return

    common.ensure_data_dir()
    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    session_id = event.get("session_id", "")

    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    command_preview = cmd[:MAX_CMD_LENGTH] if cmd else ""

    project = common.project_name_from_dir(os.environ.get("CLAUDE_PROJECT_DIR", ""))

    record = {
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "command_preview": command_preview,
        "session_id": session_id,
        "project": project,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    wt = common.extract_worktree_info(event)
    if wt:
        record["worktree"] = wt
    common.append_jsonl(common.DATA_DIR / "tool_durations.jsonl", record)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_tool_duration(event)
    except json.JSONDecodeError as e:
        print(f"[rl-anything:tool_duration] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:tool_duration] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
