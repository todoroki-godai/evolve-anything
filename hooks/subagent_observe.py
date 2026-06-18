#!/usr/bin/env python3
"""SubagentStop async hook — subagent の完了データを記録する。

stdin から Claude Code の SubagentStop イベント JSON を受け取り、
~/.claude/rl-anything/subagents.jsonl に追記する。

LLM 呼び出しは行わない（MUST NOT）。
書き込み失敗時はセッションをブロックしない（MUST NOT）。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import common

MAX_MESSAGE_LENGTH = 500


def _parse_ts(value) -> "datetime | None":
    """ISO timestamp 文字列を tz-aware datetime にパースする。失敗時は None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _count_recent_session_subagents(
    session_id: str, window_minutes: int, now: "datetime | None" = None
) -> int:
    """直近 window_minutes 分以内かつ同一セッションの distinct な subagent 数を返す。

    累積でなく時間窓で測ることで、長時間セッションの正常使用を誤検知せず、
    短時間に集中生成された暴走ループ/カスケードだけを捕捉する。
    timestamp が不明・パース不能な記録は窓内かどうか判定できないため計上しない
    （window 意味論を守り、古い記録による誤検知を防ぐ保守側に倒す）。

    #574: 記録「行数」でなく distinct な agent_id 数を数える。長命 background worker は
    idle のたびに SubagentStop を再発火し同一 agent_id が複数行 append されるため、
    行数を数えると 1 個のワーカーの再発火回数まで加算され distinct な subagent 数を
    水増しして偽の暴走警告を出す。agent_id 欠落レコードは識別子で dedup できないため
    個別カウントする（1 に潰すと暴走を見逃すので、過小評価しない保守側に倒す）。
    """
    subagents_file = common.DATA_DIR / "subagents.jsonl"
    if not subagents_file.exists():
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)
    seen_ids = set()
    unidentified = 0
    for line in subagents_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("session_id") != session_id:
            continue
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        agent_id = rec.get("agent_id") or rec.get("agent_name")
        if agent_id:
            seen_ids.add(agent_id)
        else:
            unidentified += 1
    return len(seen_ids) + unidentified


def handle_subagent_stop(event: dict) -> None:
    """SubagentStop イベントを処理する。"""
    common.ensure_data_dir()

    last_message = event.get("last_assistant_message") or ""
    if len(last_message) > MAX_MESSAGE_LENGTH:
        last_message = last_message[:MAX_MESSAGE_LENGTH]

    session_id = event.get("session_id", "")
    wf_ctx = common.read_workflow_context(session_id)

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    project = common.project_name_from_dir(project_dir) if project_dir else None

    record = {
        "agent_type": event.get("agent_type", ""),
        "agent_name": event.get("agent_name", ""),
        "agent_id": event.get("agent_id", ""),
        "last_assistant_message": last_message,
        "agent_transcript_path": event.get("agent_transcript_path", ""),
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parent_skill": wf_ctx["parent_skill"],
        "workflow_id": wf_ctx["workflow_id"],
        "project": project,
    }
    wt = common.extract_worktree_info(event)
    if wt:
        record["worktree"] = wt
    common.append_jsonl(common.DATA_DIR / "subagents.jsonl", record)

    cfg = common.load_user_config()
    threshold = int(cfg.get("subagent_warning_threshold", 5))
    window_minutes = int(cfg.get("subagent_window_minutes", 5))
    count = _count_recent_session_subagents(session_id, window_minutes)
    if count >= threshold:
        # systemMessage は user UI 向け（Claude には届かない）。
        # additionalContext は Claude のコンテキストに注入され、Claude 自身が読んで
        # 行動を変えられる（CC v2.1.163 で SubagentStop 対応）。subagent-guard.md の
        # 「閾値超過で作業を一時停止しユーザーに現状説明」を実際にエンフォースするには
        # 後者が必須。両方を出して user 可視性と Claude への行動指示を両立する。
        warning = {
            "systemMessage": (
                f"[rl-anything] 直近 {window_minutes} 分でこのセッションの subagent が"
                f" {count} 個生成されました。意図しないループが発生していないか確認してください。"
                f"（閾値: {threshold}）"
            ),
            "hookSpecificOutput": {
                "hookEventName": "SubagentStop",
                "additionalContext": (
                    f"[rl-anything subagent-guard] 直近 {window_minutes} 分でこのセッションの"
                    f" subagent が {count} 個生成され、閾値 {threshold} に達しました。"
                    "短時間に集中生成されているため subagent-guard.md に従い、"
                    "実行中の作業を一時停止し、意図しないループ/カスケード生成でないかを確認して、"
                    "ユーザーに現状を説明してください。"
                ),
            },
        }
        print(json.dumps(warning, ensure_ascii=False))


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_subagent_stop(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:subagent_observe] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:subagent_observe] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
