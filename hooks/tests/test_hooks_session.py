"""session_summary / save_state / restore_state / checkpoint 関連テスト。

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


class TestSessionSummary:
    """session_summary.py のテスト。"""

    def test_session_summary_recorded(self, patch_data_dir):
        # まず usage を書き込む
        usage_file = patch_data_dir / "usage.jsonl"
        usage_file.write_text(
            json.dumps({"session_id": "sess-010", "skill_name": "a"}) + "\n"
            + json.dumps({"session_id": "sess-010", "skill_name": "b"}) + "\n"
        )

        event = {"session_id": "sess-010"}
        session_summary.handle_stop(event)

        records = session_store.query()
        assert len(records) == 1
        record = records[0]
        assert record["session_id"] == "sess-010"
        assert record["skill_count"] == 2
        assert record["error_count"] == 0


    def test_workflow_sequence_recorded(self, patch_data_dir, tmp_path):
        """ワークフローシーケンスが workflows.jsonl に書き出される。"""
        usage_file = patch_data_dir / "usage.jsonl"
        records = [
            {
                "session_id": "sess-wfs-001",
                "skill_name": "Agent:Explore",
                "workflow_id": "wf-seqtest1",
                "parent_skill": "opsx:refine",
                "prompt": "explore the codebase structure",
                "timestamp": "2026-03-03T10:00:00+00:00",
            },
            {
                "session_id": "sess-wfs-001",
                "skill_name": "Agent:Explore",
                "workflow_id": "wf-seqtest1",
                "parent_skill": "opsx:refine",
                "prompt": "review spec requirements",
                "timestamp": "2026-03-03T10:01:00+00:00",
            },
            {
                "session_id": "sess-wfs-001",
                "skill_name": "Agent:general-purpose",
                "workflow_id": "wf-seqtest1",
                "parent_skill": "opsx:refine",
                "prompt": "implement the changes",
                "timestamp": "2026-03-03T10:02:00+00:00",
            },
        ]
        usage_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {"session_id": "sess-wfs-001"}
            session_summary.handle_stop(event)

        workflows_file = patch_data_dir / "workflows.jsonl"
        assert workflows_file.exists()
        wf = json.loads(workflows_file.read_text().strip())
        assert wf["workflow_id"] == "wf-seqtest1"
        assert wf["skill_name"] == "opsx:refine"
        assert wf["step_count"] == 3
        assert len(wf["steps"]) == 3
        assert wf["steps"][0]["tool"] == "Agent:Explore"
        assert wf["steps"][0]["intent_category"] == "code-exploration"
        assert wf["steps"][1]["intent_category"] == "spec-review"
        assert wf["steps"][2]["intent_category"] == "implementation"
        assert wf["source"] == "trace"

    def test_no_workflow_no_record(self, patch_data_dir, tmp_path):
        """ワークフローがないセッションでは workflows.jsonl に何も書き出さない。"""
        usage_file = patch_data_dir / "usage.jsonl"
        usage_file.write_text(
            json.dumps({"session_id": "sess-wfs-002", "skill_name": "my-skill"}) + "\n"
        )

        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {"session_id": "sess-wfs-002"}
            session_summary.handle_stop(event)

        workflows_file = patch_data_dir / "workflows.jsonl"
        assert not workflows_file.exists()

    def test_context_file_cleanup(self, patch_data_dir, tmp_path):
        """セッション終了時に文脈ファイルが削除される。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            ctx_path = tmp_path / "rl-anything-workflow-sess-wfs-003.json"
            ctx_path.write_text('{"skill_name":"test"}')
            assert ctx_path.exists()

            event = {"session_id": "sess-wfs-003"}
            session_summary.handle_stop(event)

            assert not ctx_path.exists()

    def test_context_file_cleanup_not_exists(self, patch_data_dir, tmp_path):
        """文脈ファイルが存在しない場合、エラーは発生しない。"""
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            event = {"session_id": "sess-wfs-004"}
            session_summary.handle_stop(event)  # no error


class TestCheckpointHelpers:
    """common.py の checkpoint ヘルパーのテスト。"""

    def test_find_latest_checkpoint_no_dir(self, patch_data_dir):
        """checkpoints/ ディレクトリが存在しない場合 None を返す。"""
        result = common.find_latest_checkpoint()
        assert result is None

    def test_find_latest_checkpoint_picks_newest(self, patch_data_dir):
        """複数 checkpoint から timestamp 最新を返す。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "sess-a.json").write_text(json.dumps({
            "session_id": "sess-a",
            "project_dir": "/proj/a",
            "timestamp": "2026-03-30T00:00:00+00:00",
        }))
        (cp_dir / "sess-b.json").write_text(json.dumps({
            "session_id": "sess-b",
            "project_dir": "/proj/a",
            "timestamp": "2026-03-31T00:00:00+00:00",
        }))
        result = common.find_latest_checkpoint("/proj/a")
        assert result is not None
        assert result["session_id"] == "sess-b"

    def test_find_latest_checkpoint_filters_by_project(self, patch_data_dir):
        """project_dir が一致しない checkpoint は除外される。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "sess-other.json").write_text(json.dumps({
            "session_id": "sess-other",
            "project_dir": "/proj/other",
            "timestamp": "2026-03-31T12:00:00+00:00",
        }))
        (cp_dir / "sess-mine.json").write_text(json.dumps({
            "session_id": "sess-mine",
            "project_dir": "/proj/mine",
            "timestamp": "2026-03-30T00:00:00+00:00",
        }))
        result = common.find_latest_checkpoint("/proj/mine")
        assert result is not None
        assert result["session_id"] == "sess-mine"

    def test_find_latest_checkpoint_legacy_fallback(self, patch_data_dir):
        """checkpoints/ が空で旧 checkpoint.json がある場合にフォールバックする。"""
        (patch_data_dir / "checkpoint.json").write_text(json.dumps({
            "session_id": "legacy",
            "timestamp": "2026-03-29T00:00:00+00:00",
        }))
        result = common.find_latest_checkpoint()
        assert result is not None
        assert result["session_id"] == "legacy"

    def test_find_latest_checkpoint_skips_corrupt_json(self, patch_data_dir):
        """壊れた JSON ファイルをスキップして継続する。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir()
        (cp_dir / "corrupt.json").write_text("NOT JSON")
        (cp_dir / "valid.json").write_text(json.dumps({
            "session_id": "valid",
            "project_dir": "/proj/a",
            "timestamp": "2026-03-31T00:00:00+00:00",
        }))
        result = common.find_latest_checkpoint("/proj/a")
        assert result is not None
        assert result["session_id"] == "valid"

    def test_find_latest_checkpoint_legacy_skipped_with_project_dir(self, patch_data_dir):
        """project_dir 指定時、旧 checkpoint.json にはフォールバックしない（汚染防止）。"""
        (patch_data_dir / "checkpoint.json").write_text(json.dumps({
            "session_id": "legacy-contaminated",
            "timestamp": "2026-03-29T00:00:00+00:00",
        }))
        result = common.find_latest_checkpoint("/proj/specific")
        assert result is None

    def test_cleanup_old_checkpoints_removes_stale(self, patch_data_dir):
        """TTL 超過の checkpoint ファイルが削除される。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir()
        stale_file = cp_dir / "stale.json"
        stale_file.write_text(json.dumps({"session_id": "stale"}))
        # mtime を 72h 前に設定
        old_mtime = time.time() - 72 * 3600
        os.utime(stale_file, (old_mtime, old_mtime))
        fresh_file = cp_dir / "fresh.json"
        fresh_file.write_text(json.dumps({"session_id": "fresh"}))

        common.cleanup_old_checkpoints()

        assert not stale_file.exists()
        assert fresh_file.exists()

    def test_cleanup_old_checkpoints_no_dir_noop(self, patch_data_dir):
        """checkpoints/ ディレクトリがない場合は何もしない。"""
        common.cleanup_old_checkpoints()  # no error


class TestSaveState:
    """save_state.py のテスト。"""

    def test_checkpoint_saved(self, patch_data_dir):
        event = {
            "session_id": "sess-020",
            "evolve_state": {"generation": 3},
        }
        save_state.handle_pre_compact(event)

        checkpoint_file = patch_data_dir / "checkpoints" / "sess-020.json"
        assert checkpoint_file.exists()
        data = json.loads(checkpoint_file.read_text())
        assert data["session_id"] == "sess-020"
        assert data["evolve_state"]["generation"] == 3

    def test_project_dir_saved(self, patch_data_dir):
        """checkpoint に project_dir フィールドが保存される。"""
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/my/project"}):
            save_state.handle_pre_compact({"session_id": "sess-pd-01"})
        data = json.loads((patch_data_dir / "checkpoints" / "sess-pd-01.json").read_text())
        assert data["project_dir"] == "/my/project"

    def test_separate_files_per_session(self, patch_data_dir):
        """異なる session_id で別ファイルに保存される。"""
        save_state.handle_pre_compact({"session_id": "sess-a"})
        save_state.handle_pre_compact({"session_id": "sess-b"})
        cp_dir = patch_data_dir / "checkpoints"
        assert (cp_dir / "sess-a.json").exists()
        assert (cp_dir / "sess-b.json").exists()

    def test_work_context_saved(self, patch_data_dir):
        """正常系: work_context が checkpoint に保存される。"""
        git_outputs = {
            ("rev-parse", "--abbrev-ref", "HEAD"): "feature/test\n",
            ("log", "--oneline", "-5"): "abc1234 fix: something\ndef5678 feat: another\n",
            ("status", "--short"): " M file1.py\n?? file2.py\n",
        }

        def fake_run(args, **kwargs):
            key = tuple(args[1:])  # skip "git"
            stdout = git_outputs.get(key, "")
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = stdout
            return result

        with mock.patch("save_state.subprocess.run", side_effect=fake_run):
            save_state.handle_pre_compact({"session_id": "sess-wc-01"})

        data = json.loads((patch_data_dir / "checkpoints" / "sess-wc-01.json").read_text())
        wc = data["work_context"]
        assert wc["git_branch"] == "feature/test"
        assert len(wc["recent_commits"]) == 2
        assert "abc1234 fix: something" in wc["recent_commits"]
        assert len(wc["uncommitted_files"]) == 2

    def test_work_context_git_failure(self, patch_data_dir):
        """git コマンド失敗時に空のフォールバック値で保存される。"""
        def fake_run(args, **kwargs):
            raise FileNotFoundError("git not found")

        with mock.patch("save_state.subprocess.run", side_effect=fake_run):
            save_state.handle_pre_compact({"session_id": "sess-wc-02"})

        data = json.loads((patch_data_dir / "checkpoints" / "sess-wc-02.json").read_text())
        wc = data["work_context"]
        assert wc["git_branch"] == ""
        assert wc["recent_commits"] == []
        assert wc["uncommitted_files"] == []

    def test_work_context_uncommitted_limit(self, patch_data_dir):
        """uncommitted_files が _MAX_UNCOMMITTED_FILES を超える場合に切り詰められる。"""
        many_files = "\n".join(f" M file{i}.py" for i in range(50))

        def fake_run(args, **kwargs):
            key = tuple(args[1:])
            outputs = {
                ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
                ("log", "--oneline", "-5"): "",
                ("status", "--short"): many_files + "\n",
            }
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = outputs.get(key, "")
            return result

        with mock.patch("save_state.subprocess.run", side_effect=fake_run):
            save_state.handle_pre_compact({"session_id": "sess-wc-03"})

        data = json.loads((patch_data_dir / "checkpoints" / "sess-wc-03.json").read_text())
        assert len(data["work_context"]["uncommitted_files"]) == 30


class TestRestoreState:
    """restore_state.py のテスト。"""

    def _write_checkpoint(self, patch_data_dir, data, session_id=None):
        """テストヘルパー: checkpoints/ にファイルを書き込む。"""
        cp_dir = patch_data_dir / "checkpoints"
        cp_dir.mkdir(exist_ok=True)
        sid = session_id or data.get("session_id", "unknown")
        (cp_dir / f"{sid}.json").write_text(json.dumps(data))

    def test_checkpoint_restored(self, patch_data_dir, capsys):
        checkpoint = {
            "session_id": "sess-030",
            "project_dir": "/proj/test",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evolve_state": {"generation": 5},
        }
        self._write_checkpoint(patch_data_dir, checkpoint)

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/proj/test"}):
            restore_state.handle_session_start({})

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["restored"] is True
        assert result["checkpoint"]["evolve_state"]["generation"] == 5

    def test_no_checkpoint_noop(self, patch_data_dir, capsys):
        restore_state.handle_session_start({})

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_checkpoint_filtered_by_project(self, patch_data_dir, capsys):
        """別プロジェクトの checkpoint は復元されない。"""
        checkpoint = {
            "session_id": "sess-other-proj",
            "project_dir": "/proj/other",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evolve_state": {"generation": 99},
        }
        self._write_checkpoint(patch_data_dir, checkpoint)

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/proj/mine"}):
            restore_state.handle_session_start({})

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_work_context_restored_with_summary(self, patch_data_dir, capsys):
        """work_context 付き checkpoint の復元でサマリーが出力される。"""
        checkpoint = {
            "session_id": "sess-040",
            "project_dir": "/proj/test",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evolve_state": {},
            "work_context": {
                "git_branch": "feature/x",
                "recent_commits": ["abc1234 fix: something"],
                "uncommitted_files": ["path/to/file1"],
            },
        }
        self._write_checkpoint(patch_data_dir, checkpoint)

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/proj/test"}):
            restore_state.handle_session_start({})

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        result = json.loads(lines[0])
        assert result["restored"] is True
        summary = "\n".join(lines[1:])
        assert "[rl-anything:restore_state] 作業コンテキスト復元:" in summary
        assert "ブランチ: feature/x" in summary
        assert "abc1234 fix: something" in summary
        assert "path/to/file1" in summary

    def test_work_context_missing_backward_compat(self, patch_data_dir, capsys):
        """work_context なしの旧 checkpoint でもエラーが発生しない。"""
        checkpoint = {
            "session_id": "sess-050",
            "project_dir": "/proj/test",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "evolve_state": {"generation": 2},
        }
        self._write_checkpoint(patch_data_dir, checkpoint)

        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/proj/test"}):
            restore_state.handle_session_start({})

        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert result["restored"] is True
        assert "作業コンテキスト復元" not in captured.out


