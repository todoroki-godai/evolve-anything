#!/usr/bin/env python3
"""PermissionDenied hook — auto mode でのパーミッション拒否を errors.jsonl に記録する。

CC v2.1.89 で追加された PermissionDenied フックイベントに対応。
auto mode classifier が拒否した操作を記録し、discover/evolve で
パーミッション設定の改善提案に活用する。
LLM 呼び出しは行わない（MUST NOT）。
"""
import json
import os
import sys
from datetime import datetime, timezone

import common


def handle_permission_denied(event: dict) -> None:
    """PermissionDenied イベントを処理する。"""
    common.ensure_data_dir()

    session_id = event.get("session_id", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    project = common.project_name_from_dir(project_dir) if project_dir else None

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})

    record = {
        "type": "permission_denied",
        "tool_name": tool_name,
        "tool_input_summary": _summarize_input(tool_name, tool_input),
        "denial_reason": event.get("denial_reason", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "project": project,
    }
    wt = common.extract_worktree_info(event)
    if wt:
        record["worktree"] = wt
    common.append_jsonl(common.DATA_DIR / "errors.jsonl", record)


def _summarize_input(tool_name: str, tool_input: dict) -> str:
    """ツール入力の要約を返す。機密情報を含めないよう短縮する。"""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:200] if cmd else ""
    if tool_name in ("Edit", "Write", "Read"):
        return tool_input.get("file_path", "")[:200]
    return str(tool_input)[:200]


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_permission_denied(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:permission_denied] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:permission_denied] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
