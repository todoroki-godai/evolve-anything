"""stop_failure.py の error_type 決定論分類テスト（#37）。

CC の StopFailure イベントは error_type を提供せず、旧実装は
`event.get("error_type", "unknown")` で常に "unknown" に落ちていた。
実カテゴリは error_message 側にあるので本文から分類する。
"""
import json
import os
from unittest import mock

import stop_failure


def test_classify_from_message_when_event_lacks_error_type():
    f = stop_failure._classify_error_type
    assert f("rate_limit", "") == "rate_limit"
    assert f("Rate limit exceeded", "unknown") == "rate_limit"
    assert f("authentication_failed", "") == "authentication_failed"
    assert f("server_error", "") == "server_error"
    assert f("invalid_request", "") == "invalid_request"
    assert f("Overloaded", "") == "overloaded"
    assert f("", "") == "unknown"
    assert f("何か未知のメッセージ", "") == "unknown"


def test_provided_error_type_takes_precedence():
    # event が有効な error_type を提供したら尊重する（将来 CC が提供した場合）
    assert stop_failure._classify_error_type("rate_limit", "custom_type") == "custom_type"
    # "unknown" は無効値扱いで本文分類にフォールバック
    assert stop_failure._classify_error_type("rate_limit", "unknown") == "rate_limit"


def test_handle_writes_classified_error_type(patch_data_dir):
    """回帰: error_message=rate_limit のとき error_type が "unknown" でなく "rate_limit"。"""
    event = {"session_id": "s1", "error_message": "rate_limit"}
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
        stop_failure.handle_stop_failure(event)
    lines = (patch_data_dir / "errors.jsonl").read_text().splitlines()
    rec = [json.loads(l) for l in lines if l.strip()]
    assert len(rec) == 1
    assert rec[0]["error_type"] == "rate_limit"  # 旧実装なら "unknown"
    assert rec[0]["error"] == "rate_limit"
    assert rec[0]["error_class"] == "tech"
