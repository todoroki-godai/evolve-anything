#!/usr/bin/env python3
"""PreToolUse async hook — Skill 呼び出し時にワークフロー文脈ファイルを書き出す。

stdin から Claude Code の PreToolUse イベント JSON を受け取り:
1. $TMPDIR/rl-anything-workflow-{session_id}.json に文脈を書き出す（observe.py 後方互換）
2. $TMPDIR/rl-anything-skill-stack-{session_id}.json にスタックを push する
   （PostToolUse で pop → parent_skill の正確な追跡を実現）

スタック方式により Skill→Skill のネスト呼び出しで親スキルを確実に特定できる。
単一ファイル方式では子スキルが上書きして親情報が消えるため、スタックが必要。

LLM 呼び出しは行わない（MUST NOT）。
書き込み失敗時はセッションをブロックしない（サイレント失敗）。
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))
import rl_common as common


def handle_pre_tool_use(event: dict) -> None:
    """PreToolUse イベントを処理する。Skill 呼び出し時に文脈ファイルとスタックを更新する。"""
    tool_input = event.get("tool_input", {})
    session_id = event.get("session_id", "")
    if not session_id:
        return

    skill_name = tool_input.get("skill", "")
    if not skill_name:
        return

    # ── スタック読み込み ──────────────────────────────────────────
    stack = common.read_skill_stack(session_id)
    parent_skill = stack[-1]["skill_name"] if stack else None
    invocation_trigger = "nested-skill" if stack else "top-level"

    workflow_id = f"wf-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    # ── スタックに push ───────────────────────────────────────────
    stack.append({
        "skill_name": skill_name,
        "workflow_id": workflow_id,
        "started_at": now,
    })
    common.write_skill_stack(session_id, stack)

    # ── 単一コンテキストファイルを更新（observe.py 後方互換） ─────
    context = {
        "skill_name": skill_name,
        "session_id": session_id,
        "workflow_id": workflow_id,
        "started_at": now,
        "invocation_trigger": invocation_trigger,
        "parent_skill": parent_skill,
    }
    ctx_path = common.workflow_context_path(session_id)
    tmp = ctx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ctx_path)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_pre_tool_use(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:workflow_context] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:workflow_context] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
