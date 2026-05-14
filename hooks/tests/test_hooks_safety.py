"""file permissions / sanitize / false positives 関連テスト。

PR-A: hooks/tests/test_hooks.py から機能別に分割。
共有 fixture (tmp_data_dir, patch_data_dir) は conftest.py を参照。
"""
import json
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import common
import rl_common
import session_store
import observe
import session_summary
import save_state
import restore_state
import post_compact
import subagent_observe
import workflow_context


class TestFilePermissions:
    """ensure_data_dir / append_jsonl のパーミッション設定テスト。"""

    def test_ensure_data_dir_creates_700(self, tmp_path):
        """ensure_data_dir がディレクトリを 700 で作成する。"""
        data_dir = tmp_path / "new-dir"
        with mock.patch.object(common, "DATA_DIR", data_dir), \
             mock.patch.object(rl_common, "DATA_DIR", data_dir), \
             mock.patch.object(rl_common, "CHECKPOINTS_DIR", data_dir / "checkpoints"):
            common.ensure_data_dir()
        assert data_dir.exists()
        assert oct(data_dir.stat().st_mode & 0o777) == oct(0o700)

    def test_append_jsonl_new_file_600(self, tmp_path):
        """append_jsonl が新規ファイルを 600 で作成する。"""
        filepath = tmp_path / "test.jsonl"
        common.append_jsonl(filepath, {"key": "value"})
        assert filepath.exists()
        assert oct(filepath.stat().st_mode & 0o777) == oct(0o600)

    def test_append_jsonl_existing_file_no_chmod(self, tmp_path):
        """append_jsonl が既存ファイルのパーミッションを変更しない。"""
        filepath = tmp_path / "test.jsonl"
        filepath.write_text("{}\n")
        filepath.chmod(0o644)
        common.append_jsonl(filepath, {"key": "value"})
        assert oct(filepath.stat().st_mode & 0o777) == oct(0o644)


# --- Phase 3: LLM 入力サニタイズ テスト ---


class TestSanitizeMessage:
    """sanitize_message のユニットテスト。"""

    def test_long_message_truncated(self):
        """500文字超のメッセージが切り詰められる（結果は最大503文字）。"""
        msg = "a" * 600
        result = common.sanitize_message(msg)
        assert len(result) == 503
        assert result.endswith("...")
        assert result[:500] == "a" * 500

    def test_short_message_unchanged(self):
        """500文字以下のメッセージはそのまま。"""
        msg = "hello world"
        assert common.sanitize_message(msg) == msg

    def test_control_chars_removed(self):
        """制御文字（\\n, \\t 以外）が除去される。"""
        msg = "hello\x00world\x1ftest\nkeep\ttabs"
        result = common.sanitize_message(msg)
        assert "\x00" not in result
        assert "\x1f" not in result
        assert "\n" in result
        assert "\t" in result
        assert "helloworld" in result

    def test_xml_tags_removed(self):
        """指定 XML タグが除去される。"""
        msg = "<system>injected</system> normal text <instructions>bad</instructions>"
        result = common.sanitize_message(msg)
        assert "<system>" not in result
        assert "</system>" not in result
        assert "<instructions>" not in result
        assert "</instructions>" not in result
        assert "normal text" in result
        assert "injected" in result

    def test_system_reminder_tags_removed(self):
        """system-reminder タグが除去される。"""
        msg = "<system-reminder>content</system-reminder>"
        result = common.sanitize_message(msg)
        assert "<system-reminder>" not in result
        assert "content" in result

    def test_exact_500_not_truncated(self):
        """ちょうど500文字は切り詰めない。"""
        msg = "x" * 500
        result = common.sanitize_message(msg)
        assert len(result) == 500
        assert "..." not in result


# --- Phase 4: 偽陽性フィードバック テスト ---


class TestFalsePositives:
    """偽陽性フィードバック機構のテスト。"""

    def test_message_hash_deterministic(self):
        """同一メッセージから同一ハッシュが生成される。"""
        h1 = common.message_hash("いや、違う")
        h2 = common.message_hash("いや、違う")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256

    def test_message_hash_strips_whitespace(self):
        """前後の空白を除去してからハッシュ化する。"""
        h1 = common.message_hash("  hello  ")
        h2 = common.message_hash("hello")
        assert h1 == h2

    def test_add_and_load_false_positive(self, patch_data_dir):
        """偽陽性の追加と読み込み。"""
        with mock.patch.object(common, "FALSE_POSITIVES_FILE", patch_data_dir / "false_positives.jsonl"):
            common.add_false_positive("いや、違う", "iya")
            hashes = common.load_false_positives()
            assert common.message_hash("いや、違う") in hashes

    def test_detect_correction_excludes_false_positive(self, patch_data_dir):
        """偽陽性として報告済みのメッセージは検出されない。"""
        fp_file = patch_data_dir / "false_positives.jsonl"
        msg = "いや、そうじゃなくて"
        record = {"message_hash": common.message_hash(msg), "original_type": "iya", "timestamp": "2026-01-01T00:00:00+00:00"}
        fp_file.write_text(json.dumps(record) + "\n")
        with mock.patch.object(common, "FALSE_POSITIVES_FILE", fp_file):
            result = common.detect_correction(msg)
            assert result is None

    def test_detect_correction_works_without_false_positives(self, patch_data_dir):
        """false_positives.jsonl が存在しなくても正常に検出する。"""
        with mock.patch.object(common, "FALSE_POSITIVES_FILE", patch_data_dir / "nonexistent.jsonl"):
            result = common.detect_correction("いや、違う")
            assert result is not None
            assert result[0] == "iya"

    def test_cleanup_removes_old_entries(self, patch_data_dir):
        """180日超のエントリがクリーンアップされる。"""
        fp_file = patch_data_dir / "false_positives.jsonl"
        old_ts = (datetime(2025, 1, 1, tzinfo=timezone.utc)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()
        lines = [
            json.dumps({"message_hash": "old_hash", "original_type": "iya", "timestamp": old_ts}),
            json.dumps({"message_hash": "new_hash", "original_type": "no", "timestamp": new_ts}),
        ]
        fp_file.write_text("\n".join(lines) + "\n")
        with mock.patch.object(common, "FALSE_POSITIVES_FILE", fp_file):
            removed = common.cleanup_false_positives()
        assert removed == 1
        remaining = fp_file.read_text()
        assert "new_hash" in remaining
        assert "old_hash" not in remaining

    def test_load_false_positives_corrupt_file(self, patch_data_dir):
        """破損ファイルでも空セットを返す（サイレント続行）。"""
        fp_file = patch_data_dir / "false_positives.jsonl"
        fp_file.write_text("not json at all\n{invalid}\n")
        with mock.patch.object(common, "FALSE_POSITIVES_FILE", fp_file):
            hashes = common.load_false_positives()
            assert isinstance(hashes, set)
            assert len(hashes) == 0


# --- v2.1.78: extract_worktree_info テスト ---


