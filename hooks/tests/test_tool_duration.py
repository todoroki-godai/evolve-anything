"""tool_duration hook のユニットテスト。"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common
import tool_duration


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "rl-anything"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    with mock.patch.object(common, "DATA_DIR", tmp_data_dir):
        yield tmp_data_dir


def make_event(**kwargs):
    base = {
        "tool_name": "Bash",
        "tool_input": {"command": "sleep 2"},
        "tool_result": {},
        "session_id": "test-session",
        "duration_ms": 2000,
    }
    base.update(kwargs)
    return json.dumps(base)


class TestSlowCommandRecording:
    """SLOW_THRESHOLD_MS 以上の duration_ms を持つイベントの記録テスト。"""

    def test_records_slow_bash_command(self, patch_data_dir):
        event = make_event(duration_ms=2500, tool_input={"command": "make build"})
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        out_file = patch_data_dir / "tool_durations.jsonl"
        assert out_file.exists()
        record = json.loads(out_file.read_text().strip())
        assert record["tool_name"] == "Bash"
        assert record["duration_ms"] == 2500
        assert record["command_preview"] == "make build"
        assert record["session_id"] == "test-session"
        assert "timestamp" in record
        assert "project" in record

    def test_records_exactly_at_threshold(self, patch_data_dir):
        event = make_event(duration_ms=1000, tool_input={"command": "echo hi"})
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        out_file = patch_data_dir / "tool_durations.jsonl"
        assert out_file.exists()
        record = json.loads(out_file.read_text().strip())
        assert record["duration_ms"] == 1000
        assert record["tool_name"] == "Bash"

    def test_truncates_long_command(self, patch_data_dir):
        long_cmd = "x" * 300
        event = make_event(duration_ms=1500, tool_input={"command": long_cmd})
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        record = json.loads((patch_data_dir / "tool_durations.jsonl").read_text().strip())
        assert len(record["command_preview"]) == 200

    def test_project_field_populated(self, patch_data_dir):
        event = make_event(duration_ms=1500)
        with mock.patch("sys.stdin") as mock_stdin, \
             mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/some/project"}):
            mock_stdin.read.return_value = event
            tool_duration.main()

        record = json.loads((patch_data_dir / "tool_durations.jsonl").read_text().strip())
        assert record["project"] == "project"

    def test_records_float_duration(self, patch_data_dir):
        event = make_event(duration_ms=1500.7)
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        record = json.loads((patch_data_dir / "tool_durations.jsonl").read_text().strip())
        assert record["duration_ms"] == 1500.7


class TestFastCommandSkipping:
    """SLOW_THRESHOLD_MS 未満の duration_ms を持つイベントは記録しない。"""

    def test_skips_fast_command(self, patch_data_dir):
        event = make_event(duration_ms=50)
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()

    def test_skips_just_below_threshold(self, patch_data_dir):
        event = make_event(duration_ms=999)
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()

    def test_skips_missing_duration(self, patch_data_dir):
        event = json.dumps({"tool_name": "Bash", "tool_input": {}, "session_id": "s"})
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()

    def test_skips_null_duration(self, patch_data_dir):
        event = json.dumps({"tool_name": "Bash", "tool_input": {}, "session_id": "s", "duration_ms": None})
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()

    def test_skips_string_duration(self, patch_data_dir):
        """duration_ms が文字列型のイベントは記録しない（型ガード）。"""
        event = json.dumps({"tool_name": "Bash", "tool_input": {}, "session_id": "s", "duration_ms": "2000"})
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()


class TestUserConfigThreshold:
    """CLAUDE_PLUGIN_OPTION_slow_threshold_ms による閾値カスタマイズのテスト。"""

    def test_custom_threshold_records_above(self, patch_data_dir):
        """環境変数で閾値を 500ms に下げると 600ms のコマンドも記録される。"""
        event = make_event(duration_ms=600)
        with mock.patch("sys.stdin") as mock_stdin, \
             mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_slow_threshold_ms": "500"}):
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert (patch_data_dir / "tool_durations.jsonl").exists()

    def test_custom_threshold_skips_below(self, patch_data_dir):
        """環境変数で閾値を 2000ms に上げると 1500ms のコマンドはスキップされる。"""
        event = make_event(duration_ms=1500)
        with mock.patch("sys.stdin") as mock_stdin, \
             mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_OPTION_slow_threshold_ms": "2000"}):
            mock_stdin.read.return_value = event
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()


class TestEdgeCases:
    """エッジケースの処理テスト。"""

    def test_tool_input_none_does_not_crash(self, patch_data_dir):
        """tool_input が JSON null のイベントを安全に処理する。"""
        event = json.dumps({
            "tool_name": "Bash",
            "tool_input": None,
            "session_id": "s",
            "duration_ms": 2000,
        })
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        # tool_input=None でも記録される（command_preview は空文字列）
        out_file = patch_data_dir / "tool_durations.jsonl"
        assert out_file.exists()
        record = json.loads(out_file.read_text().strip())
        assert record["command_preview"] == ""

    def test_tool_input_missing_command_key(self, patch_data_dir):
        """tool_input に command キーがなくても記録される。"""
        event = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"description": "run build"},
            "session_id": "s",
            "duration_ms": 1500,
        })
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = event
            tool_duration.main()

        record = json.loads((patch_data_dir / "tool_durations.jsonl").read_text().strip())
        assert record["command_preview"] == ""


class TestErrorHandling:
    """エラー時にセッションをブロックしない（サイレント失敗）。"""

    def test_invalid_json_does_not_crash(self, patch_data_dir):
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "not-json"
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()

    def test_empty_stdin_does_not_crash(self, patch_data_dir):
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = ""
            tool_duration.main()

        assert not (patch_data_dir / "tool_durations.jsonl").exists()
