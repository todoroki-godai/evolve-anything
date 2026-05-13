"""ctx_guard hook のユニットテスト (issue: feat/token-ctx-guard)。

ctx_guard はセッション末尾の usage を見て
(input_tokens + cache_read_input_tokens + cache_creation_input_tokens) / window
が閾値%を超えたら警告する。token_guard とは独立した「context window 占有率」軸。
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctx_guard


def _make_jsonl(entries: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in entries) + "\n"


def _usage_entry(input_t: int, cache_read: int = 0, cache_create: int = 0,
                 ts: str = "2026-05-13T00:00:00.000Z") -> dict:
    return {
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": input_t,
                "output_tokens": 100,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            }
        },
        "timestamp": ts,
    }


class TestCtxGuard:
    def test_no_warning_below_percent(self, tmp_path, capsys):
        jsonl = tmp_path / "session.jsonl"
        # 100K / 1M = 10% → no warn
        jsonl.write_text(_make_jsonl([_usage_entry(50000, cache_read=50000)]))
        cache = tmp_path / "ctx_cache.json"

        result = ctx_guard.check_ctx_usage(
            session_file=jsonl, cache_file=cache,
            warn_percent=20, window_tokens=1_000_000,
        )
        assert result is False
        assert capsys.readouterr().out == ""

    def test_warning_above_percent(self, tmp_path, capsys):
        jsonl = tmp_path / "session.jsonl"
        # 250K / 1M = 25% → warn
        jsonl.write_text(_make_jsonl([_usage_entry(50000, cache_read=180000, cache_create=20000)]))
        cache = tmp_path / "ctx_cache.json"

        result = ctx_guard.check_ctx_usage(
            session_file=jsonl, cache_file=cache,
            warn_percent=20, window_tokens=1_000_000,
        )
        assert result is True
        out = capsys.readouterr().out
        assert "ctx_guard" in out
        assert "25" in out  # percent
        assert "250,000" in out

    def test_uses_latest_entry_only(self, tmp_path, capsys):
        """累積でなく最新 message の usage を見る。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([
            _usage_entry(900000),  # 古いエントリ（90%）
            _usage_entry(50000, cache_read=50000),  # 最新（10%）→ warn しない
        ]))
        cache = tmp_path / "ctx_cache.json"

        result = ctx_guard.check_ctx_usage(
            session_file=jsonl, cache_file=cache,
            warn_percent=20, window_tokens=1_000_000,
        )
        assert result is False

    def test_cooldown_suppresses_repeat(self, tmp_path, capsys):
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(300000)]))
        cache = tmp_path / "ctx_cache.json"
        cache.write_text(json.dumps({"last_warned_at": time.time() - 60}))  # 1分前

        result = ctx_guard.check_ctx_usage(
            session_file=jsonl, cache_file=cache,
            warn_percent=20, window_tokens=1_000_000,
        )
        assert result is False

    def test_disabled_when_percent_zero(self, tmp_path, capsys):
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(900000)]))
        cache = tmp_path / "ctx_cache.json"

        result = ctx_guard.check_ctx_usage(
            session_file=jsonl, cache_file=cache,
            warn_percent=0, window_tokens=1_000_000,
        )
        assert result is False

    def test_no_usage_entries(self, tmp_path, capsys):
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([{"type": "user", "message": {"content": "hi"}}]))
        cache = tmp_path / "ctx_cache.json"

        result = ctx_guard.check_ctx_usage(
            session_file=jsonl, cache_file=cache,
            warn_percent=20, window_tokens=1_000_000,
        )
        assert result is False
        assert capsys.readouterr().out == ""

    def test_session_id_missing_silent(self, capsys):
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/tmp"}, clear=False):
            ctx_guard.run({})
        assert capsys.readouterr().out == ""

    def test_env_overrides(self, tmp_path):
        with mock.patch.dict(os.environ, {
            "CLAUDE_PLUGIN_OPTION_ctx_warn_percent": "50",
            "CLAUDE_PLUGIN_OPTION_ctx_window_tokens": "200000",
        }):
            assert ctx_guard.get_warn_percent() == 50
            assert ctx_guard.get_window_tokens() == 200000

    def test_env_invalid_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {
            "CLAUDE_PLUGIN_OPTION_ctx_warn_percent": "abc",
            "CLAUDE_PLUGIN_OPTION_ctx_window_tokens": "",
        }):
            assert ctx_guard.get_warn_percent() == 20
            assert ctx_guard.get_window_tokens() == 1_000_000
