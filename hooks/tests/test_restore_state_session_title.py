"""restore_state._make_session_title と sessionTitle 出力テスト。"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

_hooks_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_hooks_dir))

import restore_state


class TestMakeSessionTitle:
    def test_pj_name_and_branch(self, tmp_path):
        checkpoint = {"work_context": {"git_branch": "feat/foo"}}
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            title = restore_state._make_session_title(checkpoint)
        assert title == f"{tmp_path.name} | feat/foo"

    def test_pj_name_only(self, tmp_path):
        checkpoint = {"work_context": {}}
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            title = restore_state._make_session_title(checkpoint)
        assert title == tmp_path.name

    def test_branch_only(self):
        checkpoint = {"work_context": {"git_branch": "main"}}
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            title = restore_state._make_session_title(checkpoint)
        assert title == "main"

    def test_empty_when_no_info(self):
        checkpoint = {"work_context": {}}
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            title = restore_state._make_session_title(checkpoint)
        assert title == ""

    def test_no_work_context(self, tmp_path):
        checkpoint = {}
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            title = restore_state._make_session_title(checkpoint)
        assert title == tmp_path.name


class TestHandleSessionStartTitle:
    def test_session_title_in_output(self, tmp_path, capsys, monkeypatch):
        """sessionTitle が hookSpecificOutput に含まれる。"""
        monkeypatch.setattr(
            "common.find_latest_checkpoint",
            lambda _: {"work_context": {"git_branch": "main"}},
        )
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            restore_state.handle_session_start({})

        out = capsys.readouterr().out
        data = json.loads(out.strip().splitlines()[0])
        assert data["hookSpecificOutput"]["sessionTitle"] == f"{tmp_path.name} | main"

    def test_no_session_title_when_empty(self, capsys, monkeypatch):
        """sessionTitle が空の場合は hookSpecificOutput を出力しない。"""
        monkeypatch.setattr(
            "common.find_latest_checkpoint",
            lambda _: {"work_context": {}},
        )
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            restore_state.handle_session_start({})

        out = capsys.readouterr().out
        data = json.loads(out.strip().splitlines()[0])
        assert "hookSpecificOutput" not in data
