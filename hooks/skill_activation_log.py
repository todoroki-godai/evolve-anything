#!/usr/bin/env python3
"""PostToolUse async hook — Skill 呼び出しの invocation_trigger と parent_skill を記録する。

v2.1.121 で PostToolUse が全ツール対応になったことを活用。
workflow_context.py (PreToolUse) が push したスタックから parent_skill を取得し
skill_activations.jsonl に追記する。PostToolUse 完了後にスタックから pop する。

スタック方式により Skill→Skill のネストでも正確な parent_skill を記録できる。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))
import common
import rl_common


def _get_project() -> str | None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None
    return common.project_name_from_dir(project_dir)


def handle_post_tool_use(event: dict) -> None:
    session_id = event.get("session_id", "")
    skill_name = event.get("tool_input", {}).get("skill", "")
    if not session_id or not skill_name:
        return

    common.ensure_data_dir()

    # ── スタックから parent_skill と invocation_trigger を取得 ────
    stack = rl_common.read_skill_stack(session_id)
    parent_skill: str | None = None
    invocation_trigger = "unknown"

    if stack and stack[-1].get("skill_name") == skill_name:
        # 末尾が現在のスキル → parent を取得して pop
        parent_skill = stack[-2]["skill_name"] if len(stack) >= 2 else None
        invocation_trigger = "nested-skill" if parent_skill else "top-level"
        rl_common.write_skill_stack(session_id, stack[:-1])
    else:
        # スタック不整合（並列呼び出し等）→ コンテキストファイルにフォールバック
        ctx_path = common.workflow_context_path(session_id)
        if ctx_path.exists():
            try:
                raw = json.loads(ctx_path.read_text(encoding="utf-8"))
                invocation_trigger = raw.get("invocation_trigger", "unknown")
                parent_skill = raw.get("parent_skill")
            except (json.JSONDecodeError, OSError):
                pass

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill": skill_name,
        "session_id": session_id,
        "invocation_trigger": invocation_trigger,
        "parent_skill": parent_skill,
        "project": _get_project(),
    }

    out_file = common.DATA_DIR / "skill_activations.jsonl"
    with out_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_post_tool_use(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:skill_activation_log] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:skill_activation_log] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
