"""agent_quality モジュールのテスト。

scan_agents(), check_quality(), check_upstream() を検証する。
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# プロジェクトルートを sys.path に追加
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib.agent_quality import (
    ANTI_PATTERNS,
    BEST_PRACTICES,
    MIN_DESCRIPTION_LENGTH,
    AgentInfo,
    check_quality,
    check_upstream,
    scan_agents,
)


# --- Fixtures ---


@pytest.fixture
def global_agents_dir(tmp_path):
    """グローバルエージェントディレクトリ。"""
    d = tmp_path / ".claude" / "agents"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def project_agents_dir(tmp_path):
    """プロジェクトエージェントディレクトリ。"""
    d = tmp_path / "myproject" / ".claude" / "agents"
    d.mkdir(parents=True)
    return d


def _write_agent(agents_dir, name, content):
    """エージェント .md を作成するヘルパー。"""
    path = agents_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _good_agent():
    """品質チェック全パスするエージェント（公式パターン準拠）。"""
    return """\
---
name: code-reviewer
description: Expert code review specialist. Proactively reviews code for quality, security, and maintainability. Use immediately after writing or modifying code.
tools: Read, Grep, Glob, Bash
model: sonnet
---

# Code Reviewer Agent

You are a senior code reviewer ensuring high standards of code quality and security.

When invoked:
1. Run git diff to see recent changes
2. Focus on modified files
3. Begin review immediately

Review checklist:
- Code is clear and readable
- Functions and variables are well-named
- No duplicated code
- Proper error handling
- No exposed secrets or API keys

Provide feedback organized by priority:
- Critical issues (must fix)
- Warnings (should fix)
- Suggestions (consider improving)

Include specific examples of how to fix issues.
"""


def _minimal_agent():
    """frontmatter のみの最小エージェント。"""
    return """\
---
name: Minimal
description: A minimal agent.
---

Do things.
"""


def _no_frontmatter_agent():
    """frontmatter なしのエージェント。"""
    return """\
# My Agent

Just do stuff.
"""


# --- scan_agents tests ---


class TestScanAgents:
    """scan_agents() のテスト。"""

    def test_scan_global_agents(self, global_agents_dir, tmp_path):
        _write_agent(global_agents_dir, "reviewer", _good_agent())
        _write_agent(global_agents_dir, "helper", _minimal_agent())

        with mock.patch("lib.agent_quality.Path.home", return_value=tmp_path):
            agents = scan_agents()

        assert len(agents) == 2
        names = {a.name for a in agents}
        assert names == {"reviewer", "helper"}
        assert all(a.scope == "global" for a in agents)

    def test_scan_project_agents(self, project_agents_dir, tmp_path):
        _write_agent(project_agents_dir, "local-bot", _good_agent())
        project_root = tmp_path / "myproject"

        with mock.patch("lib.agent_quality.Path.home", return_value=tmp_path):
            agents = scan_agents(project_root=project_root)

        project_agents = [a for a in agents if a.scope == "project"]
        assert len(project_agents) == 1
        assert project_agents[0].name == "local-bot"

    def test_scan_no_agents_dir(self, tmp_path):
        """agents ディレクトリが存在しない場合は空リスト。"""
        with mock.patch("lib.agent_quality.Path.home", return_value=tmp_path):
            agents = scan_agents()

        assert agents == []

    def test_scan_dedup_project_over_global(
        self, global_agents_dir, project_agents_dir, tmp_path
    ):
        """同名エージェントが global と project にある場合、project 優先。"""
        _write_agent(global_agents_dir, "shared", _good_agent())
        _write_agent(project_agents_dir, "shared", _minimal_agent())
        project_root = tmp_path / "myproject"

        with mock.patch("lib.agent_quality.Path.home", return_value=tmp_path):
            agents = scan_agents(project_root=project_root)

        shared = [a for a in agents if a.name == "shared"]
        assert len(shared) == 1
        assert shared[0].scope == "project"


# --- check_quality tests ---


class TestCheckQuality:
    """check_quality() のテスト。"""

    def test_good_agent_no_issues(self, global_agents_dir, tmp_path):
        path = _write_agent(global_agents_dir, "reviewer", _good_agent())
        agent = AgentInfo(name="reviewer", path=path, scope="global")

        result = check_quality(agent)

        assert result["issues"] == []
        assert result["score"] > 0.7

    def test_missing_frontmatter(self, global_agents_dir, tmp_path):
        path = _write_agent(global_agents_dir, "bad", _no_frontmatter_agent())
        agent = AgentInfo(name="bad", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "missing_frontmatter" in issue_types

    def test_weak_output_spec(self, global_agents_dir, tmp_path):
        """出力形式の指示がない場合に検出される。"""
        content = """\
---
name: vague-bot
description: A bot that does things without specifying output format at all.
tools: Read
---

# Vague Bot

You are a bot that does things.
Help the user with whatever they need.
"""
        path = _write_agent(global_agents_dir, "vague", content)
        agent = AgentInfo(name="vague", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "weak_output_spec" in issue_types

    def test_good_output_spec_inline(self, global_agents_dir, tmp_path):
        """公式パターン: セクション見出しなしでも本文中に出力指示があればOK。"""
        content = """\
---
name: reviewer
description: Reviews code for quality and best practices. Use after code changes.
tools: Read, Grep
---

# Reviewer

When invoked, analyze code and provide specific, actionable feedback.

Provide feedback organized by priority:
- Critical issues (must fix)
- Warnings (should fix)

Include specific examples of how to fix issues.
"""
        path = _write_agent(global_agents_dir, "reviewer", content)
        agent = AgentInfo(name="reviewer", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "weak_output_spec" not in issue_types

    def test_weak_trigger_description(self, global_agents_dir, tmp_path):
        """description が短すぎる場合に検出される。"""
        content = """\
---
name: short-desc
description: Does stuff.
tools: Read
---

# Short Desc Agent

Do things.
"""
        path = _write_agent(global_agents_dir, "short", content)
        agent = AgentInfo(name="short", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "weak_trigger_description" in issue_types

    def test_missing_tools_restriction(self, global_agents_dir, tmp_path):
        """tools フィールド未設定の場合に検出される。"""
        content = """\
---
name: no-tools
description: An agent without tools restriction that inherits everything.
---

# No Tools Agent

Do whatever.
"""
        path = _write_agent(global_agents_dir, "notools", content)
        agent = AgentInfo(name="notools", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "missing_tools_restriction" in issue_types

    def test_no_checklist(self, global_agents_dir, tmp_path):
        content = """\
---
name: no-check
description: Agent without checklist or numbered steps for verification.
tools: Read
---

# NoCheck Agent

You do code reviews. Look at the code and say what you think.
"""
        path = _write_agent(global_agents_dir, "nocheck", content)
        agent = AgentInfo(name="nocheck", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "no_checklist" in issue_types

    def test_kitchen_sink(self, global_agents_dir, tmp_path):
        """過度に多くのセクションがあるエージェント。"""
        sections = "\n\n".join(
            [f"## Section {i}\n\nContent for section {i}." for i in range(15)]
        )
        content = f"""\
---
name: Kitchen Sink
description: Does everything.
---

# Kitchen Sink Agent

{sections}
"""
        path = _write_agent(global_agents_dir, "sink", content)
        agent = AgentInfo(name="sink", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "kitchen_sink" in issue_types

    def test_vague_mission(self, global_agents_dir, tmp_path):
        """曖昧な表現が多いエージェント。"""
        content = """\
---
name: flexible-bot
description: A flexible agent that can do anything and everything you need in any situation.
tools: Read
---

# Flexible Bot

You are a flexible, versatile agent that handles anything.
Do whatever the user asks for, anything is fine.
Be as flexible as possible and do everything.
"""
        path = _write_agent(global_agents_dir, "flexible", content)
        agent = AgentInfo(name="flexible", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "vague_mission" in issue_types

    def test_best_practice_suggestions(self, global_agents_dir, tmp_path):
        path = _write_agent(global_agents_dir, "minimal", _minimal_agent())
        agent = AgentInfo(name="minimal", path=path, scope="global")

        result = check_quality(agent)

        assert len(result["suggestions"]) > 0

    def test_line_count_warning(self, global_agents_dir, tmp_path):
        """行数が多すぎるエージェント。"""
        big_body = "\n".join([f"Line {i} of content" for i in range(500)])
        content = f"""\
---
name: BigAgent
description: A very large agent.
---

# Big Agent

{big_body}
"""
        path = _write_agent(global_agents_dir, "big", content)
        agent = AgentInfo(name="big", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "bloated_agent" in issue_types


# --- check_upstream tests ---


class TestCheckUpstream:
    """check_upstream() のテスト。"""

    def test_first_check_saves_hash(self, tmp_path):
        """初回チェック: ハッシュを保存する。"""
        state_file = tmp_path / "agent-brushup-state.json"

        with mock.patch(
            "lib.agent_quality._fetch_latest_commit_hash",
            return_value="abc123",
        ):
            result = check_upstream(state_file=state_file)

        assert result["status"] == "first_check"
        assert result["current_hash"] == "abc123"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["upstream_commit_hash"] == "abc123"

    def test_no_update(self, tmp_path):
        """ハッシュが同じ場合: 更新なし。"""
        state_file = tmp_path / "agent-brushup-state.json"
        state_file.write_text(json.dumps({"upstream_commit_hash": "abc123"}))

        with mock.patch(
            "lib.agent_quality._fetch_latest_commit_hash",
            return_value="abc123",
        ):
            result = check_upstream(state_file=state_file)

        assert result["status"] == "no_update"

    def test_has_update(self, tmp_path):
        """ハッシュが異なる場合: 更新あり。"""
        state_file = tmp_path / "agent-brushup-state.json"
        state_file.write_text(json.dumps({"upstream_commit_hash": "old123"}))

        with mock.patch(
            "lib.agent_quality._fetch_latest_commit_hash",
            return_value="new456",
        ):
            result = check_upstream(state_file=state_file)

        assert result["status"] == "updated"
        assert result["previous_hash"] == "old123"
        assert result["current_hash"] == "new456"
        # state ファイルも更新されるべき
        state = json.loads(state_file.read_text())
        assert state["upstream_commit_hash"] == "new456"

    def test_api_failure_graceful(self, tmp_path):
        """gh api 失敗時: graceful skip。"""
        state_file = tmp_path / "agent-brushup-state.json"

        with mock.patch(
            "lib.agent_quality._fetch_latest_commit_hash",
            return_value=None,
        ):
            result = check_upstream(state_file=state_file)

        assert result["status"] == "error"
        assert not state_file.exists()


# --- Constants tests ---


class TestConstants:
    """定数のテスト。"""

    def test_anti_patterns_not_empty(self):
        assert len(ANTI_PATTERNS) >= 5

    def test_best_practices_not_empty(self):
        assert len(BEST_PRACTICES) >= 4
