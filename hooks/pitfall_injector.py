#!/usr/bin/env python3
"""UserPromptSubmit hook — HASP-style pitfall inject。

セッション内エラーが error_preflight_threshold に達したとき、
last_skill の pitfall テキストを stdout に出力して Claude のコンテキストに追加する。

inject タイミング: UserPromptSubmit のためエラー発生から 1 ターン遅延あり（CC API 制約）。
同一 session × skill では 1 度のみ inject する（重複防止）。

LLM 呼び出しは行わない（MUST NOT）。
例外発生時は stderr に警告を出力し exit 0 で終了する（MUST）。
"""
import json
import os
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import common  # noqa: E402
from pitfall_manager.injector import (  # noqa: E402
    count_recent_errors,
    get_pitfall_for_skill,
    is_already_injected,
    mark_injected,
)

_DEFAULT_THRESHOLD = 3


def _get_threshold() -> int:
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_error_preflight_threshold", "")
    try:
        return max(1, int(raw)) if raw else _DEFAULT_THRESHOLD
    except ValueError:
        return _DEFAULT_THRESHOLD


def handle_user_prompt_submit(event: dict) -> None:
    """UserPromptSubmit イベントを処理する。"""
    session_id = event.get("session_id", "")
    if not session_id:
        return

    threshold = _get_threshold()
    error_count = count_recent_errors(session_id)
    if error_count < threshold:
        return

    skill_name = common.read_last_skill(session_id)
    if not skill_name:
        return

    if is_already_injected(session_id, skill_name):
        return

    pitfall_text = get_pitfall_for_skill(skill_name)
    if not pitfall_text:
        return

    safe_name = skill_name.replace("\n", "").replace("\r", "")
    header = f"[evolve-anything pitfall-inject: {safe_name}]\n"
    print(header + pitfall_text, flush=True)
    mark_injected(session_id, skill_name)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_user_prompt_submit(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[evolve-anything:pitfall_injector] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[evolve-anything:pitfall_injector] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
