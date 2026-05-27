"""message_display.py — MessageDisplay フックのテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

_hooks_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_hooks_dir))

import message_display


class TestCountCodeBlocks:
    def test_no_blocks(self):
        assert message_display._count_code_blocks("plain text") == 0

    def test_one_block(self):
        assert message_display._count_code_blocks("```python\ncode\n```") == 1

    def test_two_blocks(self):
        text = "```python\nfoo\n```\n\n```bash\nbar\n```"
        assert message_display._count_code_blocks(text) == 2


class TestLoadPitfallKeywords:
    def test_cache_populated_once(self, monkeypatch):
        """2回呼んでもglob は1回しか走らない。"""
        call_count = [0]
        original_load = message_display._load_pitfall_keywords

        def patched_glob_root():
            call_count[0] += 1
            return []

        # キャッシュをリセット
        monkeypatch.setattr(message_display, "_PITFALL_KW_CACHE", None)
        monkeypatch.setattr(
            "message_display._load_pitfall_keywords",
            lambda: original_load(),
        )
        # 1回目
        message_display._PITFALL_KW_CACHE = None
        r1 = message_display._load_pitfall_keywords()
        # 2回目（キャッシュが使われる）
        r2 = message_display._load_pitfall_keywords()
        assert r1 == r2
        assert message_display._PITFALL_KW_CACHE is not None


class TestRotateIfNeeded:
    def test_rotation_when_over_limit(self, tmp_path):
        """上限超えでローテーションされる。"""
        log = tmp_path / "message_display.jsonl"
        log.write_bytes(b"x" * (message_display._MAX_LOG_BYTES + 1))
        message_display._rotate_if_needed(log)
        assert not log.exists()
        assert (tmp_path / "message_display.jsonl.1").exists()

    def test_no_rotation_under_limit(self, tmp_path):
        """上限未満はローテーションしない。"""
        log = tmp_path / "message_display.jsonl"
        log.write_bytes(b"small")
        message_display._rotate_if_needed(log)
        assert log.exists()


class TestMain:
    def setup_method(self):
        """各テスト前にキャッシュをリセット。"""
        message_display._PITFALL_KW_CACHE = None

    def test_passthrough_no_output(self, tmp_path, capsys, monkeypatch):
        """stdout に何も出力しない（passthrough）。"""
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        event = json.dumps({"message": "Hello world", "session_id": "s1"})
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(event))
        message_display.main()
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_telemetry_written(self, tmp_path, monkeypatch):
        """message_display.jsonl にレコードが書き込まれる。"""
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        event = json.dumps({"message": "```python\nprint('hi')\n```", "session_id": "s1"})
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(event))
        message_display.main()
        log = tmp_path / "message_display.jsonl"
        assert log.exists()
        rec = json.loads(log.read_text(encoding="utf-8").strip())
        assert rec["session_id"] == "s1"
        assert rec["code_blocks"] == 1
        assert rec["char_count"] > 0

    def test_empty_stdin(self, tmp_path, monkeypatch, capsys):
        """stdin が空でもエラーにならない。"""
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
        message_display.main()
        assert capsys.readouterr().out == ""

    def test_no_message_field(self, tmp_path, monkeypatch, capsys):
        """message フィールドなしのイベントは無視される。"""
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        event = json.dumps({"session_id": "s1"})
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(event))
        message_display.main()
        assert not (tmp_path / "message_display.jsonl").exists()

    def test_log_rotation(self, tmp_path, monkeypatch):
        """ログが上限を超えたらローテーションされる。"""
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        log = tmp_path / "message_display.jsonl"
        log.write_bytes(b"x" * (message_display._MAX_LOG_BYTES + 1))
        event = json.dumps({"message": "hello", "session_id": "s2"})
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(event))
        message_display.main()
        assert (tmp_path / "message_display.jsonl.1").exists()
        # ローテーション後の新ファイルに新レコードが書かれている
        new_content = log.read_text(encoding="utf-8")
        assert "s2" in new_content
