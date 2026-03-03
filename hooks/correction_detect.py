#!/usr/bin/env python3
"""UserPromptSubmit hook — CJK/英語の修正パターンを検出し corrections.jsonl に記録する。

stdin から Claude Code の UserPromptSubmit イベント JSON を受け取り、
ユーザーの発話テキストから修正パターンをマッチする。

LLM 呼び出しは行わない（MUST NOT）。
例外発生時は stderr に警告を出力し exit 0 で終了する（MUST）。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import common


def handle_user_prompt_submit(event: dict) -> None:
    """UserPromptSubmit イベントを処理する。"""
    common.ensure_data_dir()

    session_id = event.get("session_id", "")
    if not session_id:
        return

    # ユーザーの発話テキストを取得
    message = ""
    raw_content = event.get("message", {})
    if isinstance(raw_content, str):
        message = raw_content
    elif isinstance(raw_content, dict):
        content = raw_content.get("content", "")
        if isinstance(content, str):
            message = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text.strip():
                        message = text
                        break

    if not message.strip():
        return

    # 修正パターン検出
    result = common.detect_correction(message)
    if result is None:
        return

    correction_type, confidence = result

    # 直前スキルを取得
    last_skill = common.read_last_skill(session_id)

    # corrections.jsonl に追記
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "correction_type": correction_type,
        "message": message.strip(),
        "last_skill": last_skill,
        "confidence": confidence,
        "timestamp": now,
        "session_id": session_id,
    }
    common.append_jsonl(common.DATA_DIR / "corrections.jsonl", record)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_user_prompt_submit(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:correction] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:correction] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
