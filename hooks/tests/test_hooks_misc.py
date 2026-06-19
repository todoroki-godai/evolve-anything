"""instructions_loaded / stop_failure / data_dir fallback / post_compact 関連テスト。

PR-A: hooks/tests/test_hooks.py から機能別に分割。
共有 fixture (tmp_data_dir, patch_data_dir) は conftest.py を参照。
"""
import importlib
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
import instructions_loaded
import stop_failure


class TestInstructionsLoaded:
    """instructions_loaded.py のテスト。"""

    def test_first_call_records(self, patch_data_dir):
        """初回呼び出しで sessions テーブルに記録される。"""
        event = {"session_id": "sess-il-001"}
        instructions_loaded.handle_instructions_loaded(event)

        records = session_store.query()
        assert len(records) == 1
        record = records[0]
        assert record["type"] == "instructions_loaded"
        assert record["session_id"] == "sess-il-001"

    def test_second_call_dedup(self, patch_data_dir):
        """同一セッションの2回目は記録しない。"""
        event = {"session_id": "sess-il-002"}
        instructions_loaded.handle_instructions_loaded(event)
        instructions_loaded.handle_instructions_loaded(event)

        records = session_store.query()
        assert len(records) == 1

    def test_different_sessions_both_record(self, patch_data_dir):
        """異なるセッションはそれぞれ記録される。"""
        instructions_loaded.handle_instructions_loaded({"session_id": "sess-il-003"})
        instructions_loaded.handle_instructions_loaded({"session_id": "sess-il-004"})

        records = session_store.query()
        assert len(records) == 2

    def test_empty_session_id_noop(self, patch_data_dir):
        """session_id が空の場合は何もしない。"""
        instructions_loaded.handle_instructions_loaded({"session_id": ""})
        sessions_file = patch_data_dir / "sessions.jsonl"
        assert not sessions_file.exists()

    def test_stale_flag_cleaned(self, patch_data_dir):
        """STALE_FLAG_TTL_HOURS 超過のフラグは削除される。"""
        tmp_dir = patch_data_dir / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        stale_flag = tmp_dir / f"{common.INSTRUCTIONS_LOADED_FLAG_PREFIX}stale-session"
        stale_flag.write_text("stale-session")
        # mtime を25時間前に設定
        old_time = time.time() - (25 * 3600)
        os.utime(stale_flag, (old_time, old_time))

        # 新しいセッションの処理で stale が掃除される
        instructions_loaded.handle_instructions_loaded({"session_id": "sess-il-005"})
        assert not stale_flag.exists()


# --- v2.1.78: stop_failure.py テスト ---

import stop_failure


class TestStopFailure:
    """stop_failure.py のテスト。"""

    def test_rate_limit_recorded(self, patch_data_dir):
        """rate limit エラーが errors.jsonl に記録される。"""
        event = {
            "session_id": "sess-sf-001",
            "error_type": "rate_limit",
            "error_message": "Rate limit exceeded",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        assert errors_file.exists()
        record = json.loads(errors_file.read_text().strip())
        assert record["type"] == "api_error"
        assert record["error_type"] == "rate_limit"
        assert record["error"] == "Rate limit exceeded"
        assert record["session_id"] == "sess-sf-001"

    def test_auth_failure_recorded(self, patch_data_dir):
        """認証失敗エラーが記録される。"""
        event = {
            "session_id": "sess-sf-002",
            "error_type": "auth_failure",
            "error_message": "Invalid API key",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["error_type"] == "auth_failure"

    def test_worktree_attached(self, patch_data_dir):
        """worktree 情報が付与される。"""
        event = {
            "session_id": "sess-sf-003",
            "error_type": "rate_limit",
            "error_message": "Rate limit",
            "worktree": {"name": "wt-sf", "branch": "feat/sf", "path": "/tmp/wt"},
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["worktree"] == {"name": "wt-sf", "branch": "feat/sf"}

    def test_unknown_error_type(self, patch_data_dir):
        """error_type が未設定の場合は 'unknown'。"""
        event = {
            "session_id": "sess-sf-004",
            "error_message": "Something went wrong",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["error_type"] == "unknown"

    # --- error_class フィールド (AgentErrorTaxonomy #148) ---

    def test_error_class_is_tech_for_rate_limit(self, patch_data_dir):
        """rate_limit は error_class='tech' に分類される。"""
        event = {
            "session_id": "sess-sf-005",
            "error_type": "rate_limit",
            "error_message": "Rate limit exceeded",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["error_class"] == "tech"

    def test_error_class_is_tech_for_auth_failure(self, patch_data_dir):
        """auth_failure は error_class='tech' に分類される。"""
        event = {
            "session_id": "sess-sf-006",
            "error_type": "auth_failure",
            "error_message": "Invalid API key",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["error_class"] == "tech"

    def test_error_class_is_tech_for_timeout(self, patch_data_dir):
        """timeout は error_class='tech' に分類される。"""
        event = {
            "session_id": "sess-sf-007",
            "error_type": "timeout",
            "error_message": "Request timed out",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["error_class"] == "tech"

    def test_error_class_is_tech_for_unknown(self, patch_data_dir):
        """unknown error_type も error_class='tech' に分類される。"""
        event = {
            "session_id": "sess-sf-008",
            "error_message": "Something went wrong",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["error_class"] == "tech"

    def test_error_layer_absent_for_tech(self, patch_data_dir):
        """tech エラーには error_layer フィールドが含まれない。"""
        event = {
            "session_id": "sess-sf-009",
            "error_type": "rate_limit",
            "error_message": "Rate limit exceeded",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert "error_layer" not in record

    def test_existing_fields_preserved(self, patch_data_dir):
        """既存フィールド (type, error_type, error, session_id) が互換維持される。"""
        event = {
            "session_id": "sess-sf-010",
            "error_type": "rate_limit",
            "error_message": "Rate limit exceeded",
        }
        stop_failure.handle_stop_failure(event)

        errors_file = patch_data_dir / "errors.jsonl"
        record = json.loads(errors_file.read_text().strip())
        assert record["type"] == "api_error"
        assert record["error_type"] == "rate_limit"
        assert record["error"] == "Rate limit exceeded"
        assert record["session_id"] == "sess-sf-010"


# --- v2.1.78: DATA_DIR CLAUDE_PLUGIN_DATA フォールバック テスト ---


class TestDataDirFallback:
    """DATA_DIR の CLAUDE_PLUGIN_DATA フォールバックテスト。

    common.py は rl_common の re-exporter になったため、DATA_DIR の実体は
    rl_common にある。reload 時は rl_common → common の順で行う。
    """

    def test_plugin_data_env_used(self, tmp_path):
        """CLAUDE_PLUGIN_DATA が設定されている場合はそちらが使われる。"""
        plugin_data = str(tmp_path / "plugin-data")
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": plugin_data}):
            importlib.reload(rl_common)
            importlib.reload(common)
            assert rl_common.DATA_DIR == Path(plugin_data)
        # 元に戻す
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        importlib.reload(rl_common)
        importlib.reload(common)

    def test_fallback_when_unset(self):
        """CLAUDE_PLUGIN_DATA 未設定時は従来パスにフォールバック。"""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            importlib.reload(rl_common)
            importlib.reload(common)
            assert common.DATA_DIR == Path.home() / ".claude" / "evolve-anything"


class TestPostCompact:
    """post_compact.py のテスト。"""

    def test_injects_system_message_from_checkpoint(self, patch_data_dir, capsys):
        """PostCompact 時に checkpoint から systemMessage を注入する。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "session_id": "sess-pc-01",
            "project_dir": "/my/project",
            "timestamp": "2026-04-13T00:00:00+00:00",
            "evolve_state": {},
            "corrections_snapshot": [],
            "work_context": {
                "git_branch": "feat/test",
                "recent_commits": ["abc1234 fix: something"],
                "uncommitted_files": ["M file1.py"],
            },
        }
        (cp_dir / "sess-pc-01.json").write_text(json.dumps(checkpoint))

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/my/project"}):
            post_compact.handle_post_compact({"session_id": "sess-pc-01"})

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "feat/test" in output["systemMessage"]
        assert "abc1234" in output["systemMessage"]

    def test_no_output_without_checkpoint(self, patch_data_dir, capsys):
        """checkpoint がない場合は何も出力しない。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/no/project"}):
            post_compact.handle_post_compact({"session_id": "sess-pc-02"})

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_includes_uncommitted_files(self, patch_data_dir, capsys):
        """uncommitted_files がメッセージに含まれる。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "session_id": "sess-pc-03",
            "project_dir": "/my/project",
            "timestamp": "2026-04-13T00:00:00+00:00",
            "evolve_state": {},
            "corrections_snapshot": [],
            "work_context": {
                "git_branch": "main",
                "recent_commits": [],
                "uncommitted_files": ["M src/app.py", "?? new_file.py"],
            },
        }
        (cp_dir / "sess-pc-03.json").write_text(json.dumps(checkpoint))

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/my/project"}):
            post_compact.handle_post_compact({"session_id": "sess-pc-03"})

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "src/app.py" in output["systemMessage"]
        assert "new_file.py" in output["systemMessage"]

    def test_empty_work_context(self, patch_data_dir, capsys):
        """work_context が空でも systemMessage を出力する。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "session_id": "sess-pc-04",
            "project_dir": "/my/project",
            "timestamp": "2026-04-13T00:00:00+00:00",
            "evolve_state": {},
            "corrections_snapshot": [],
            "work_context": {
                "git_branch": "",
                "recent_commits": [],
                "uncommitted_files": [],
            },
        }
        (cp_dir / "sess-pc-04.json").write_text(json.dumps(checkpoint))

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/my/project"}):
            post_compact.handle_post_compact({"session_id": "sess-pc-04"})

        captured = capsys.readouterr()
        # checkpoint exists but no work context → still outputs a minimal message
        output = json.loads(captured.out)
        assert "systemMessage" in output
