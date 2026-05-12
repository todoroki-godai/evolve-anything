"""token_guard hook のユニットテスト (issue #34)。"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import token_guard


def _make_jsonl(entries: list[dict]) -> str:
    """usage エントリを含む .jsonl 文字列を生成する。"""
    return "\n".join(json.dumps(e) for e in entries) + "\n"


def _usage_entry(input_t: int, output_t: int, ts: str = "2026-05-12T00:00:00.000Z") -> dict:
    return {
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cache_read_input_tokens": 0,
            }
        },
        "timestamp": ts,
    }


class TestTokenGuard:
    def test_no_warning_below_threshold(self, tmp_path, capsys):
        """閾値未満では出力なし。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(10000, 5000)]))
        cache = tmp_path / "cache.json"

        result = token_guard.check_token_usage(
            session_file=jsonl,
            cache_file=cache,
            threshold=50000,
        )
        assert result is False
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_warning_above_threshold(self, tmp_path, capsys):
        """閾値超えで stdout に警告を出力する。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(40000, 20000)]))
        cache = tmp_path / "cache.json"

        result = token_guard.check_token_usage(
            session_file=jsonl,
            cache_file=cache,
            threshold=50000,
        )
        assert result is True
        captured = capsys.readouterr()
        assert "token_guard" in captured.out
        assert "60,000" in captured.out  # 40000+20000

    def test_no_warning_within_cooldown(self, tmp_path, capsys):
        """5分以内の再警告は出力しない。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(40000, 20000)]))
        cache = tmp_path / "cache.json"

        # 1回目（警告が出る）
        token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=50000)
        capsys.readouterr()

        # 2回目（4分後、クールダウン中）
        cache_data = json.loads(cache.read_text())
        cache_data["last_warned_at"] = time.time() - 240  # 4分前
        cache.write_text(json.dumps(cache_data))

        result = token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=50000)
        assert result is False
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_warning_after_cooldown(self, tmp_path, capsys):
        """5分経過後は再警告を出力する。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(40000, 20000)]))
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({
            "total": 60000,
            "byte_offset": 0,
            "last_warned_at": time.time() - 310,  # 5分10秒前
        }))

        result = token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=50000)
        assert result is True
        captured = capsys.readouterr()
        assert "token_guard" in captured.out

    def test_cache_diff_read(self, tmp_path):
        """byte_offset キャッシュで差分だけ読む。"""
        jsonl = tmp_path / "session.jsonl"
        first_entry = _make_jsonl([_usage_entry(10000, 5000)])
        jsonl.write_text(first_entry)
        cache = tmp_path / "cache.json"

        # 初回読み込み
        token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=999999)
        first_cache = json.loads(cache.read_text())
        assert first_cache["total"] == 15000
        first_offset = first_cache["byte_offset"]
        assert first_offset > 0

        # 追記
        jsonl.write_text(first_entry + _make_jsonl([_usage_entry(5000, 2000)]))

        token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=999999)
        second_cache = json.loads(cache.read_text())
        assert second_cache["total"] == 22000  # 15000 + 7000
        assert second_cache["byte_offset"] > first_offset

    def test_session_id_missing_exits_silently(self, capsys):
        """session_id が取得できない場合は出力なしで終了する。"""
        event = {}  # session_id なし
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/some/path"}, clear=False):
            token_guard.run(event)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_missing_usage_field_skipped(self, tmp_path, capsys):
        """message.usage フィールドがない行を KeyError なくスキップする。"""
        jsonl = tmp_path / "session.jsonl"
        entries = [
            {"type": "user", "message": {"content": "hello"}},  # usage なし
            _usage_entry(40000, 20000),
            {"type": "tool_result", "content": "ok"},  # usage なし
        ]
        jsonl.write_text(_make_jsonl(entries))
        cache = tmp_path / "cache.json"

        # 例外が発生しないこと
        token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=50000)

    def test_custom_threshold_from_env(self, tmp_path, capsys):
        """CLAUDE_PLUGIN_OPTION_token_warn_threshold で閾値を変更できる。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(30000, 10000)]))
        cache = tmp_path / "cache.json"

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_token_warn_threshold": "30000"}):
            result = token_guard.check_token_usage(
                session_file=jsonl,
                cache_file=cache,
                threshold=token_guard.get_threshold(),
            )
        assert result is True

    def test_pace_calculation(self, tmp_path, capsys):
        """tokens/分 のペースが出力に含まれる。"""
        jsonl = tmp_path / "session.jsonl"
        # 開始タイムスタンプを2分前に設定
        ts_start = "2026-05-12T00:00:00.000Z"
        jsonl.write_text(_make_jsonl([
            _usage_entry(40000, 20000, ts=ts_start),
        ]))
        cache = tmp_path / "cache.json"

        token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=50000)
        captured = capsys.readouterr()
        assert "tokens/分" in captured.out

    def test_tmp_write_failure_silent_fallback(self, tmp_path, capsys):
        """/tmp 書き込み失敗時は silent fallback（例外なし、末尾読み）。"""
        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text(_make_jsonl([_usage_entry(40000, 20000)]))
        # 書き込み不能なディレクトリのパスを渡す
        cache = Path("/nonexistent_dir/cache.json")

        # 例外が発生しないこと
        token_guard.check_token_usage(session_file=jsonl, cache_file=cache, threshold=50000)
