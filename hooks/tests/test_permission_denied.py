"""permission_denied hook のテスト。"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

import common
import rl_common


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "evolve-anything"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    # store_write は SoT の rl_common.DATA_DIR を call-time 参照する（ADR-049 / #55 wave 2）。
    # errors 書込がゲート経由になるため SoT も同じ tmp に向ける（additive・挙動不変）。
    with mock.patch.object(common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(common, "CHECKPOINTS_DIR", tmp_data_dir / "checkpoints"), \
         mock.patch.object(rl_common, "DATA_DIR", tmp_data_dir):
        yield tmp_data_dir


class TestPermissionDenied:
    """permission_denied.py のテスト。"""

    def test_errors_routed_through_store_write(self, patch_data_dir):
        """permission_denied の errors 書込は store_write 単一ゲート経由（#55 wave 2）。"""
        import permission_denied

        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "session_id": "sess-pd-sw",
            "denial_reason": "auto_mode_classifier",
        }
        with mock.patch.object(common, "store_write") as m_sw, \
             mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}):
            permission_denied.handle_permission_denied(event)
        assert m_sw.call_count == 1
        assert m_sw.call_args.args[0] == "errors.jsonl"
        assert m_sw.call_args.args[1]["session_id"] == "sess-pd-sw"

    def test_records_permission_denied_event(self, patch_data_dir):
        """PermissionDenied イベントが errors.jsonl に type:permission_denied で記録される。"""
        import permission_denied

        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "session_id": "sess-pd-001",
            "denial_reason": "auto_mode_classifier",
        }
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}):
            permission_denied.handle_permission_denied(event)

        errors_path = patch_data_dir / "errors.jsonl"
        assert errors_path.exists()
        record = json.loads(errors_path.read_text().strip())
        assert record["type"] == "permission_denied"
        assert record["tool_name"] == "Bash"
        assert record["project"] == "test-project"
        assert record["session_id"] == "sess-pd-001"
        assert "denial_reason" in record

    def test_records_without_project_dir(self, patch_data_dir):
        """CLAUDE_PROJECT_DIR が未設定でも記録できる。"""
        import permission_denied

        event = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/etc/passwd"},
            "session_id": "sess-pd-002",
        }
        with mock.patch.dict(os.environ, {}, clear=True):
            # CLAUDE_PROJECT_DIR が存在しないことを保証
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            permission_denied.handle_permission_denied(event)

        errors_path = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_path.read_text().strip())
        assert record["type"] == "permission_denied"
        assert record["project"] is None

    def test_worktree_info_included(self, patch_data_dir):
        """worktree 情報がある場合はレコードに含まれる。"""
        import permission_denied

        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force"},
            "session_id": "sess-pd-003",
            "worktree": {
                "name": "feature-x",
                "branch": "feat/x",
                "path": "/tmp/worktrees/feature-x",
            },
        }
        permission_denied.handle_permission_denied(event)

        errors_path = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_path.read_text().strip())
        assert "worktree" in record
        assert record["worktree"]["name"] == "feature-x"

    def test_file_path_none_does_not_crash(self, patch_data_dir):
        """tool_input.file_path が明示的に None でも TypeError にならない（#30 同型）。

        `.get("file_path", "")` のデフォルトは「キー欠落」しか守らず、値が None の
        とき `None[:200]` で TypeError になる。None 合体で守る。
        """
        import permission_denied

        event = {
            "tool_name": "Edit",
            "tool_input": {"file_path": None},
            "session_id": "sess-pd-none",
        }
        permission_denied.handle_permission_denied(event)  # should not raise

        errors_path = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_path.read_text().strip())
        assert record["type"] == "permission_denied"

    def test_main_reads_stdin(self, patch_data_dir):
        """main() が stdin から JSON を読んで処理する。"""
        import permission_denied

        event = {
            "tool_name": "Write",
            "tool_input": {},
            "session_id": "sess-pd-004",
        }
        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = json.dumps(event)
            permission_denied.main()

        errors_path = patch_data_dir / "errors.jsonl"
        assert errors_path.exists()

    def test_main_handles_empty_stdin(self, patch_data_dir):
        """main() が空の stdin でもクラッシュしない。"""
        import permission_denied

        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = ""
            permission_denied.main()  # should not raise

        errors_path = patch_data_dir / "errors.jsonl"
        assert not errors_path.exists()

    def test_main_handles_invalid_json(self, patch_data_dir):
        """main() が不正 JSON でもクラッシュしない。"""
        import permission_denied

        with mock.patch("sys.stdin") as mock_stdin:
            mock_stdin.read.return_value = "not json"
            permission_denied.main()  # should not raise
