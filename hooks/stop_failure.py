#!/usr/bin/env python3
"""StopFailure hook — API エラーによるセッション中断を errors.jsonl に記録する。

rate limit、認証失敗等の API エラーでターンが終了した際に発火する。
LLM 呼び出しは行わない（MUST NOT）。
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import common

def _classify_error_class(error_type: str) -> str:
    """error_type から error_class を同期判定する。

    hook では tech エラーのみ同期分類する。behavioral 分類は
    reflect スキルが遅延付与するため、ここでは付与しない。
    """
    # 現時点では全 error_type を tech として記録する
    return "tech"


def _classify_error_type(error_message: str, provided: str = "") -> str:
    """error_type を決定論で確定する（#37）。

    CC の StopFailure イベントは `error_type` を提供しないため、
    `event.get("error_type", "unknown")` は構造的に常に "unknown" に落ちていた。
    実カテゴリは `error_message`（rate_limit / authentication_failed 等のクリーンな
    ラベル、または人間可読な本文）に入っているので、本文から分類する。
    LLM は使わない（MUST NOT）。

    provided が有効値（非空・非 "unknown"）ならそれを尊重する。
    """
    if provided and provided != "unknown":
        return provided
    msg = (error_message or "").lower()
    if not msg.strip():
        return "unknown"
    # HTTP ステータスコードは word-boundary で照合する。ベアの部分一致（"400" in msg）は
    # "4001ms" / "5001 tokens" 等を誤検知するため re.search(r"\bNNN\b") を使う（#37 follow-up）。
    if "rate" in msg and "limit" in msg:
        return "rate_limit"
    if "overload" in msg:
        return "overloaded"
    if "authenticat" in msg or "api key" in msg or re.search(r"\b401\b", msg):
        return "authentication_failed"
    if "invalid_request" in msg or "invalid request" in msg or re.search(r"\b400\b", msg):
        return "invalid_request"
    if (
        "server_error" in msg
        or "server error" in msg
        or "internal server" in msg  # ベアの "internal" は "internal cache" 等を誤検知するため除外
        or re.search(r"\b500\b", msg)
    ):
        return "server_error"
    return "unknown"


def handle_stop_failure(event: dict) -> None:
    """StopFailure イベントを処理する。"""
    common.ensure_data_dir()

    session_id = event.get("session_id", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    project = common.project_name_from_dir(project_dir) if project_dir else None

    error_message = str(event.get("error_message", "") or event.get("error", ""))
    error_type = _classify_error_type(error_message, event.get("error_type", ""))
    error_class = _classify_error_class(error_type)

    record = {
        "type": "api_error",
        "tool_name": "",
        "skill_name": "",
        "error_type": error_type,
        "error_class": error_class,
        # error_layer は tech エラーでは付与しない（behavioral 分類は reflect が遅延付与）
        "error": error_message[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "project": project,
    }
    wt = common.extract_worktree_info(event)
    if wt:
        record["worktree"] = wt
    common.append_jsonl(common.DATA_DIR / "errors.jsonl", record)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_stop_failure(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[evolve-anything:stop_failure] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[evolve-anything:stop_failure] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
