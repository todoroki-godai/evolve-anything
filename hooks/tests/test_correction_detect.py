"""correction_detect hooks のユニットテスト。"""
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common
import correction_detect


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "rl-anything"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    with mock.patch.object(common, "DATA_DIR", tmp_data_dir):
        yield tmp_data_dir


class TestDetectCorrection:
    """common.detect_correction() のテスト。"""

    def test_iya_pattern(self):
        result = common.detect_correction("いや、そうじゃなくて optimize を使って")
        assert result is not None
        assert result[0] == "iya"
        assert result[1] == 0.85

    def test_chigau_pattern(self):
        result = common.detect_correction("違う、そのアプローチではない")
        assert result is not None
        assert result[0] == "chigau"
        assert result[1] == 0.85

    def test_souja_nakute_pattern(self):
        result = common.detect_correction("そうじゃなくてこっちを使う")
        assert result is not None
        assert result[0] == "souja-nakute"
        assert result[1] == 0.80

    def test_no_pattern(self):
        result = common.detect_correction("no, don't use that approach")
        assert result is not None
        assert result[0] == "no"
        assert result[1] == 0.75

    def test_dont_pattern(self):
        result = common.detect_correction("don't do that")
        assert result is not None
        assert result[0] == "dont"
        assert result[1] == 0.75

    def test_stop_pattern(self):
        result = common.detect_correction("stop doing that")
        assert result is not None
        assert result[0] == "stop"
        assert result[1] == 0.80

    def test_no_match(self):
        result = common.detect_correction("ありがとう、完璧です")
        assert result is None

    def test_empty_string(self):
        result = common.detect_correction("")
        assert result is None

    def test_question_excluded(self):
        """疑問文は除外される。"""
        result = common.detect_correction("いや、それでいいの？")
        assert result is None

    def test_question_mark_ascii(self):
        result = common.detect_correction("no, is that right?")
        assert result is None


class TestLastSkill:
    """common.write_last_skill / read_last_skill のテスト。"""

    def test_write_and_read(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-ls-001", "commit")
            result = common.read_last_skill("sess-ls-001")
            assert result == "commit"

    def test_read_nonexistent(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            result = common.read_last_skill("sess-ls-none")
            assert result is None

    def test_read_expired(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-ls-exp", "test")
            path = common.last_skill_path("sess-ls-exp")
            old_time = time.time() - (25 * 60 * 60)
            os.utime(path, (old_time, old_time))
            result = common.read_last_skill("sess-ls-exp")
            assert result is None


class TestCorrectionDetectHook:
    """correction_detect.py のフックテスト。"""

    def test_japanese_correction_detected(self, patch_data_dir):
        event = {
            "session_id": "sess-cd-001",
            "message": {"content": "いや、そうじゃなくて skill-evolve を使って"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert record["correction_type"] == "iya"
        assert record["confidence"] == 0.85
        assert record["session_id"] == "sess-cd-001"
        assert record["last_skill"] is None

    def test_english_correction_detected(self, patch_data_dir):
        event = {
            "session_id": "sess-cd-002",
            "message": {"content": "No, don't use that approach"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
        record = json.loads(corrections_file.read_text().strip())
        assert record["correction_type"] == "no"

    def test_question_not_detected(self, patch_data_dir):
        """疑問文は corrections に追記されない。"""
        event = {
            "session_id": "sess-cd-003",
            "message": {"content": "いや、それでいいの？"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert not corrections_file.exists()

    def test_no_correction_for_normal_text(self, patch_data_dir):
        event = {
            "session_id": "sess-cd-004",
            "message": {"content": "ありがとう、完璧です"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        assert not corrections_file.exists()

    def test_with_last_skill(self, patch_data_dir, tmp_path):
        """直前スキルが紐付けられる。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            common.write_last_skill("sess-cd-005", "commit")
            event = {
                "session_id": "sess-cd-005",
                "message": {"content": "いや、違うコマンドを使って"},
            }
            correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        assert record["last_skill"] == "commit"

    def test_schema_compliance(self, patch_data_dir):
        """レコードが正式スキーマに準拠する。"""
        event = {
            "session_id": "sess-cd-schema",
            "message": {"content": "いや、そうじゃなくて"},
        }
        correction_detect.handle_user_prompt_submit(event)

        corrections_file = patch_data_dir / "corrections.jsonl"
        record = json.loads(corrections_file.read_text().strip())
        # 全必須フィールドの存在チェック
        assert "correction_type" in record
        assert "message" in record
        assert "last_skill" in record
        assert "confidence" in record
        assert "timestamp" in record
        assert "session_id" in record
        # source フィールドはリアルタイム検出には付与しない
        assert "source" not in record

    def test_silent_failure_on_bad_json(self, patch_data_dir, capsys):
        """不正 JSON でも exit 0（サイレント失敗）。"""
        # main() をテスト
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "NOT VALID JSON{{{"
            correction_detect.main()
        captured = capsys.readouterr()
        assert "[rl-anything:correction] parse error" in captured.err

    def test_empty_session_id_noop(self, patch_data_dir):
        event = {
            "session_id": "",
            "message": {"content": "いや、違う"},
        }
        correction_detect.handle_user_prompt_submit(event)
        corrections_file = patch_data_dir / "corrections.jsonl"
        assert not corrections_file.exists()

    def test_content_as_list(self, patch_data_dir):
        """content がリスト形式でも処理できる。"""
        event = {
            "session_id": "sess-cd-list",
            "message": {
                "content": [
                    {"type": "text", "text": "いや、違うよ"}
                ]
            },
        }
        correction_detect.handle_user_prompt_submit(event)
        corrections_file = patch_data_dir / "corrections.jsonl"
        assert corrections_file.exists()
