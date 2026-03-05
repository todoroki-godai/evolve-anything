"""classify_agent_type のユニットテスト。"""

import logging
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from agent_classifier import BUILTIN_AGENT_NAMES, classify_agent_type


class TestClassifyAgentType:
    """classify_agent_type の分類テスト。"""

    def test_builtin_explore(self):
        assert classify_agent_type("Explore") == "builtin"

    def test_builtin_plan(self):
        assert classify_agent_type("Plan") == "builtin"

    def test_builtin_general_purpose(self):
        assert classify_agent_type("general-purpose") == "builtin"

    def test_custom_project(self, tmp_path):
        project_root = tmp_path / "project"
        agents_dir = project_root / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "my-agent.md").write_text("# my-agent")
        assert classify_agent_type("my-agent", project_root=project_root) == "custom_project"

    def test_custom_global(self, tmp_path, monkeypatch):
        global_agents = tmp_path / ".claude" / "agents"
        global_agents.mkdir(parents=True)
        (global_agents / "global-agent.md").write_text("# global-agent")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert classify_agent_type("global-agent") == "custom_global"

    def test_both_dirs_project_wins(self, tmp_path, monkeypatch):
        """両方のディレクトリに存在する場合は project 優先。"""
        # global
        global_agents = tmp_path / ".claude" / "agents"
        global_agents.mkdir(parents=True)
        (global_agents / "shared-agent.md").write_text("# global")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # project
        project_root = tmp_path / "project"
        project_agents = project_root / ".claude" / "agents"
        project_agents.mkdir(parents=True)
        (project_agents / "shared-agent.md").write_text("# project")

        assert classify_agent_type("shared-agent", project_root=project_root) == "custom_project"

    def test_unknown_fallback_to_builtin(self, tmp_path, monkeypatch):
        """未知の Agent は builtin にフォールバック。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert classify_agent_type("never-heard-of") == "builtin"

    def test_directory_not_exist(self, tmp_path, monkeypatch):
        """ディレクトリが存在しない場合、例外なく builtin を返す。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "nonexistent")
        assert classify_agent_type("some-agent") == "builtin"

    def test_io_error_logs_warning(self, tmp_path, monkeypatch, caplog):
        """I/O エラー時は WARNING ログを出力しスキップ。"""
        # Permission error をシミュレート
        global_agents = tmp_path / ".claude" / "agents"
        global_agents.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        original_is_file = Path.is_file

        def raise_on_is_file(self):
            if "agents" in str(self):
                raise PermissionError("Access denied")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", raise_on_is_file)

        with caplog.at_level(logging.WARNING):
            result = classify_agent_type("test-agent")

        assert result == "builtin"
        assert any("scan failed" in msg for msg in caplog.messages)


class TestBuiltinAgentNames:
    """BUILTIN_AGENT_NAMES の内容テスト。"""

    def test_contains_known_agents(self):
        assert "Explore" in BUILTIN_AGENT_NAMES
        assert "Plan" in BUILTIN_AGENT_NAMES
        assert "general-purpose" in BUILTIN_AGENT_NAMES

    def test_is_set(self):
        assert isinstance(BUILTIN_AGENT_NAMES, set)
