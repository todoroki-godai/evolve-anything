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
    KNOWLEDGE_HARDCODING_LOW_THRESHOLD,
    KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD,
    MIN_DESCRIPTION_LENGTH,
    AgentInfo,
    check_quality,
    check_upstream,
    scan_agents,
)
from lib.agent_quality import check_model_pin  # noqa: F401 — new in #449
from lib.agent_quality import check_tools_grant_divergence  # noqa: F401 — new in #130
from lib.agent_quality import check_ask_before_fallback  # noqa: F401 — new in #192


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

    def test_knowledge_hardcoding_low(self, global_agents_dir, tmp_path):
        """ハードコード候補が low 閾値以上で issue が出る（severity=low）。"""
        # **project**: ... 形式を閾値以上並べる
        lines = "\n".join(
            f"- **project-{i}**: NestJS service, uses Node.js v18, path /srv/app-{i}/index.ts"
            for i in range(KNOWLEDGE_HARDCODING_LOW_THRESHOLD)
        )
        content = f"""\
---
name: hardcoded-bot
description: Agent with hardcoded project knowledge. Use this for project-specific tasks.
tools: Read
---

## Project Knowledge

{lines}
"""
        path = _write_agent(global_agents_dir, "hardcoded", content)
        agent = AgentInfo(name="hardcoded", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "knowledge_hardcoding" in issue_types
        issue = next(i for i in result["issues"] if i["type"] == "knowledge_hardcoding")
        assert issue["severity"] in ("low", "medium")

    def test_knowledge_hardcoding_medium(self, global_agents_dir, tmp_path):
        """ハードコード候補が medium 閾値以上で severity=medium になる。"""
        lines = "\n".join(
            f"- **project-{i}**: NestJS service (v{i}.0), path /srv/app-{i}/index.ts"
            for i in range(KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD)
        )
        content = f"""\
---
name: heavy-hardcoded
description: Agent with many hardcoded project-specific facts. Use for project tasks.
tools: Read
---

## Project Knowledge

{lines}
"""
        path = _write_agent(global_agents_dir, "heavy", content)
        agent = AgentInfo(name="heavy", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "knowledge_hardcoding" in issue_types
        issue = next(i for i in result["issues"] if i["type"] == "knowledge_hardcoding")
        assert issue["severity"] == "medium"

    def test_knowledge_hardcoding_absent_on_clean_agent(self, global_agents_dir, tmp_path):
        """ハードコードが少ない正常なエージェントでは knowledge_hardcoding が出ない。"""
        path = _write_agent(global_agents_dir, "clean", _good_agent())
        agent = AgentInfo(name="clean", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "knowledge_hardcoding" not in issue_types

    def test_jit_file_references_suggestion_on_minimal(self, global_agents_dir, tmp_path):
        """JIT識別子戦略のベストプラクティスが欠けていれば suggestion に出る。"""
        path = _write_agent(global_agents_dir, "minimal", _minimal_agent())
        agent = AgentInfo(name="minimal", path=path, scope="global")

        result = check_quality(agent)

        suggestion_patterns = {s["pattern"] for s in result["suggestions"]}
        assert "jit_file_references" in suggestion_patterns

    def test_jit_file_references_no_suggestion_when_present(self, global_agents_dir, tmp_path):
        """JIT鉄則が明示されていれば suggestion に出ない。"""
        content = """\
---
name: jit-bot
description: Agent that checks files before answering. Use for codebase questions.
tools: Read, Grep
---

## Dynamic Knowledge Protocol

Before answering, always Read the relevant file to confirm identifiers.
記憶に頼らず必ずファイルを確認してから回答すること。

## Output

Provide specific file paths and line numbers in feedback.
"""
        path = _write_agent(global_agents_dir, "jit", content)
        agent = AgentInfo(name="jit", path=path, scope="global")

        result = check_quality(agent)

        suggestion_patterns = {s["pattern"] for s in result["suggestions"]}
        assert "jit_file_references" not in suggestion_patterns


# --- check_upstream tests ---


class TestCheckUpstream:
    """check_upstream() のテスト。"""

    def test_first_check_saves_hash(self, tmp_path):
        """初回チェック: ハッシュを保存する。"""
        state_file = tmp_path / "agent-brushup-state.json"

        with mock.patch(
            "agent_quality_upstream._fetch_latest_commit_hash",
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
            "agent_quality_upstream._fetch_latest_commit_hash",
            return_value="abc123",
        ):
            result = check_upstream(state_file=state_file)

        assert result["status"] == "no_update"

    def test_has_update(self, tmp_path):
        """ハッシュが異なる場合: 更新あり。"""
        state_file = tmp_path / "agent-brushup-state.json"
        state_file.write_text(json.dumps({"upstream_commit_hash": "old123"}))

        with mock.patch(
            "agent_quality_upstream._fetch_latest_commit_hash",
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
            "agent_quality_upstream._fetch_latest_commit_hash",
            return_value=None,
        ):
            result = check_upstream(state_file=state_file)

        assert result["status"] == "error"
        assert not state_file.exists()


# --- check_model_pin tests ---


class TestCheckModelPin:
    """check_model_pin() のテスト: exact model ID pin 検出。"""

    def test_exact_id_pin_detected(self, global_agents_dir, tmp_path):
        """exact model ID（claude-opus-4-8 等）は stale リスク警告として検出される。"""
        content = """\
---
name: pinned-bot
description: An agent that uses an exact model ID pin.
tools: Read
model: claude-opus-4-8
---

# Pinned Bot

1. Do this
2. Do that
3. Review output

Provide feedback organized by priority.
Include specific examples of how to fix issues.
"""
        path = _write_agent(global_agents_dir, "pinned", content)
        agent = AgentInfo(name="pinned", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is True
        assert "claude-opus-4-8" in result["current_value"]
        assert result["file"] == str(path)
        assert result["recommended_alias"] is not None

    def test_exact_id_pin_with_version_number(self, global_agents_dir, tmp_path):
        """claude- 始まりかつバージョン数字付きのパターンを検出する。"""
        content = """\
---
name: versioned-bot
description: Uses a versioned model ID like claude-sonnet-4-6.
tools: Read
model: claude-sonnet-4-6
---

# Versioned Bot

1. Step one
2. Step two
3. Step three

Provide specific, actionable feedback.
"""
        path = _write_agent(global_agents_dir, "versioned", content)
        agent = AgentInfo(name="versioned", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is True
        assert "claude-sonnet-4-6" in result["current_value"]

    def test_alias_not_flagged_sonnet(self, global_agents_dir, tmp_path):
        """エイリアス 'sonnet' は警告しない。"""
        path = _write_agent(global_agents_dir, "alias-sonnet", _good_agent())
        agent = AgentInfo(name="alias-sonnet", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is False

    def test_alias_not_flagged_opus(self, global_agents_dir, tmp_path):
        """エイリアス 'opus' は警告しない。"""
        content = """\
---
name: opus-bot
description: An agent using the opus alias without version number.
tools: Read
model: opus
---

# Opus Bot

1. Step one
2. Step two
3. Step three

Provide specific, actionable feedback.
"""
        path = _write_agent(global_agents_dir, "opus-alias", content)
        agent = AgentInfo(name="opus-alias", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is False

    def test_alias_not_flagged_haiku(self, global_agents_dir, tmp_path):
        """エイリアス 'haiku' は警告しない。"""
        content = """\
---
name: haiku-bot
description: An agent using haiku alias for quick tasks.
tools: Read
model: haiku
---

# Haiku Bot

1. Step one
2. Step two
3. Step three

Provide feedback on results.
"""
        path = _write_agent(global_agents_dir, "haiku-alias", content)
        agent = AgentInfo(name="haiku-alias", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is False

    def test_alias_not_flagged_inherit(self, global_agents_dir, tmp_path):
        """エイリアス 'inherit' は警告しない。"""
        content = """\
---
name: inherit-bot
description: An agent that inherits the model from the parent context.
tools: Read
model: inherit
---

# Inherit Bot

1. Step one
2. Step two
3. Step three

Provide feedback on results.
"""
        path = _write_agent(global_agents_dir, "inherit-alias", content)
        agent = AgentInfo(name="inherit-alias", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is False

    def test_no_model_field_not_flagged(self, global_agents_dir, tmp_path):
        """model: 未指定（フィールドなし）は警告しない。"""
        path = _write_agent(global_agents_dir, "no-model", _minimal_agent())
        agent = AgentInfo(name="no-model", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is False

    def test_fable_alias_not_flagged(self, global_agents_dir, tmp_path):
        """エイリアス 'fable' は警告しない。"""
        content = """\
---
name: fable-bot
description: An agent using the fable alias for creative tasks.
tools: Read
model: fable
---

# Fable Bot

1. Step one
2. Step two
3. Step three

Provide feedback on results.
"""
        path = _write_agent(global_agents_dir, "fable-alias", content)
        agent = AgentInfo(name="fable-alias", path=path, scope="global")

        result = check_model_pin(agent)

        assert result["pinned"] is False

    def test_check_quality_includes_model_pin_warning(self, global_agents_dir, tmp_path):
        """check_quality() の issues に exact_model_id_pin が含まれる。"""
        content = """\
---
name: pinned-quality
description: An agent that uses exact model ID in quality check context.
tools: Read
model: claude-haiku-4-0
---

# Pinned Quality Bot

1. Do this first
2. Do that second
3. Review results

Provide feedback organized by priority.
Include specific examples of how to fix issues.
"""
        path = _write_agent(global_agents_dir, "pinned-quality", content)
        agent = AgentInfo(name="pinned-quality", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "exact_model_id_pin" in issue_types
        issue = next(i for i in result["issues"] if i["type"] == "exact_model_id_pin")
        assert issue["severity"] == "medium"
        assert "claude-haiku-4-0" in issue["detail"]

    def test_check_quality_no_model_pin_warning_for_alias(self, global_agents_dir, tmp_path):
        """check_quality() でエイリアス使用時は exact_model_id_pin が出ない。"""
        path = _write_agent(global_agents_dir, "clean-model", _good_agent())
        agent = AgentInfo(name="clean-model", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "exact_model_id_pin" not in issue_types


# --- check_tools_grant_divergence tests ---


class TestCheckToolsGrantDivergence:
    """check_tools_grant_divergence() のテスト: memory: あり + tools に Write/Edit なしの検出。"""

    def test_memory_without_write_edit_flagged(self, global_agents_dir, tmp_path):
        """memory: あり + tools に Write/Edit なし → diverged=True。"""
        content = """\
---
name: advisor-bot
description: An advisory-only agent that keeps persistent memory but declares no write tools.
tools: Read, Grep, Glob
memory: user
---

# Advisor Bot

1. Read the code
2. Analyze it
3. Report findings

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "advisor", content)
        agent = AgentInfo(name="advisor", path=path, scope="global")

        result = check_tools_grant_divergence(agent)

        assert result["diverged"] is True
        assert result["has_memory"] is True
        assert result["file"] == str(path)
        assert "Write" not in result["declared_tools"]
        assert "Edit" not in result["declared_tools"]

    def test_memory_with_write_not_flagged(self, global_agents_dir, tmp_path):
        """memory: あり + tools に Write あり → diverged=False（乖離なし）。"""
        content = """\
---
name: writer-bot
description: An agent with memory that already declares Write in its tools list.
tools: Read, Write, Grep
memory: user
---

# Writer Bot

1. Read the code
2. Write output
3. Verify

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "writer", content)
        agent = AgentInfo(name="writer", path=path, scope="global")

        result = check_tools_grant_divergence(agent)

        assert result["diverged"] is False

    def test_memory_with_edit_not_flagged(self, global_agents_dir, tmp_path):
        """memory: あり + tools に Edit あり → diverged=False。"""
        content = """\
---
name: editor-bot
description: An agent with memory that declares Edit in its tools list explicitly.
tools: Read, Edit
memory: user
---

# Editor Bot

1. Read the file
2. Edit it
3. Verify

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "editor", content)
        agent = AgentInfo(name="editor", path=path, scope="global")

        result = check_tools_grant_divergence(agent)

        assert result["diverged"] is False

    def test_no_memory_not_flagged(self, global_agents_dir, tmp_path):
        """memory: なし → diverged=False（対象外）。"""
        path = _write_agent(global_agents_dir, "reviewer", _good_agent())
        agent = AgentInfo(name="reviewer", path=path, scope="global")

        result = check_tools_grant_divergence(agent)

        assert result["diverged"] is False
        assert result["has_memory"] is False

    def test_no_tools_field_not_flagged(self, global_agents_dir, tmp_path):
        """tools: 宣言自体が無い（全ツール継承）→ 対象外（誤検知回避）。"""
        content = """\
---
name: inherit-tools-bot
description: An agent with memory but no tools field, inheriting all tools by default.
memory: user
---

# Inherit Tools Bot

1. Do this
2. Do that
3. Review

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "inherit-tools", content)
        agent = AgentInfo(name="inherit-tools", path=path, scope="global")

        result = check_tools_grant_divergence(agent)

        assert result["diverged"] is False

    def test_tools_as_yaml_list(self, global_agents_dir, tmp_path):
        """tools が YAML リスト形式でも Write/Edit 判定が効く。"""
        content = """\
---
name: list-tools-bot
description: An agent whose tools are declared as a YAML block list, with memory enabled.
tools:
  - Read
  - Grep
  - Glob
memory: user
---

# List Tools Bot

1. Read the code
2. Analyze
3. Report

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "list-tools", content)
        agent = AgentInfo(name="list-tools", path=path, scope="global")

        result = check_tools_grant_divergence(agent)

        assert result["diverged"] is True
        assert "Read" in result["declared_tools"]

    def test_check_quality_includes_tools_grant_divergence(self, global_agents_dir, tmp_path):
        """check_quality() の issues に tools_grant_divergence が含まれる。"""
        content = """\
---
name: advisor-quality
description: An advisory agent with memory and no write tools declared for quality context.
tools: Read, Grep
memory: user
---

# Advisor Quality Bot

1. Read the code
2. Analyze it
3. Report findings

Provide feedback organized by priority.
Include specific examples of how to fix issues.
"""
        path = _write_agent(global_agents_dir, "advisor-quality", content)
        agent = AgentInfo(name="advisor-quality", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "tools_grant_divergence" in issue_types
        issue = next(i for i in result["issues"] if i["type"] == "tools_grant_divergence")
        assert "Write/Edit" in issue["detail"]

    def test_check_quality_no_divergence_for_no_memory(self, global_agents_dir, tmp_path):
        """check_quality() で memory: なしなら tools_grant_divergence が出ない。"""
        path = _write_agent(global_agents_dir, "clean-tools", _good_agent())
        agent = AgentInfo(name="clean-tools", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "tools_grant_divergence" not in issue_types


# --- check_ask_before_fallback tests ---


class TestCheckAskBeforeFallback:
    """check_ask_before_fallback() のテスト: worker 系 agent の ask-before-fallback 明文化検査（#192）。"""

    def test_worker_without_section_flagged(self, global_agents_dir, tmp_path):
        """worker 名 + ask-before-fallback / 確認質問 の記述なし → missing=True。"""
        content = """\
---
name: impl-worker
description: Implementation worker that receives a scoped task and implements it in a worktree.
tools: Read, Write, Edit, Bash
---

# Impl Worker

1. Read the task spec
2. Implement it
3. Commit

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "impl-worker", content)
        agent = AgentInfo(name="impl-worker", path=path, scope="global")

        result = check_ask_before_fallback(agent)

        assert result["missing"] is True
        assert result["is_worker"] is True
        assert result["file"] == str(path)

    def test_worker_with_ask_before_fallback_wording_not_flagged(self, global_agents_dir, tmp_path):
        """worker 名 + 'ask-before-fallback' 表記あり → missing=False。"""
        content = """\
---
name: impl-worker
description: Implementation worker that receives a scoped task and implements it in a worktree.
tools: Read, Write, Edit, Bash
---

# Impl Worker

**Missing referenced artifact — ask-before-fallback.** If a referenced
artifact is missing, ask the orchestrator instead of silently recreating it.

1. Read the task spec
2. Implement it
3. Commit

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "impl-worker", content)
        agent = AgentInfo(name="impl-worker", path=path, scope="global")

        result = check_ask_before_fallback(agent)

        assert result["missing"] is False
        assert result["is_worker"] is True

    def test_worker_with_kakunin_shitsumon_wording_not_flagged(self, global_agents_dir, tmp_path):
        """worker 名 + '確認質問' 表記あり → missing=False。"""
        content = """\
---
name: design-worker
description: Design worker that drafts a design doc from a scoped task spec.
tools: Read, Write
---

# Design Worker

参照物が見つからない場合は自走せず、選択肢+タイムアウト付きデフォルトを添えて
オーケストレーターに確認質問すること。

1. Read the task spec
2. Draft the design
3. Report

Provide feedback organized by priority.
"""
        path = _write_agent(global_agents_dir, "design-worker", content)
        agent = AgentInfo(name="design-worker", path=path, scope="global")

        result = check_ask_before_fallback(agent)

        assert result["missing"] is False
        assert result["is_worker"] is True

    def test_non_worker_without_section_not_flagged(self, global_agents_dir, tmp_path):
        """非 worker 名 + 記述なし → 対象外（is_worker=False, missing=False）。"""
        path = _write_agent(global_agents_dir, "code-reviewer", _good_agent())
        agent = AgentInfo(name="code-reviewer", path=path, scope="global")

        result = check_ask_before_fallback(agent)

        assert result["missing"] is False
        assert result["is_worker"] is False

    def test_check_quality_includes_missing_ask_before_fallback(self, global_agents_dir, tmp_path):
        """check_quality() の issues に missing_ask_before_fallback + agent path が含まれる。"""
        content = """\
---
name: build-worker
description: Build worker that compiles a scoped task into working code changes.
tools: Read, Write, Edit, Bash
---

# Build Worker

1. Read the task spec
2. Implement it
3. Commit

Provide feedback organized by priority.
Include specific examples of how to fix issues.
"""
        path = _write_agent(global_agents_dir, "build-worker", content)
        agent = AgentInfo(name="build-worker", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "missing_ask_before_fallback" in issue_types
        issue = next(i for i in result["issues"] if i["type"] == "missing_ask_before_fallback")
        assert str(path) in issue["detail"]

    def test_check_quality_no_issue_for_non_worker(self, global_agents_dir, tmp_path):
        """check_quality() で非 worker 名なら missing_ask_before_fallback が出ない。"""
        path = _write_agent(global_agents_dir, "clean-reviewer", _good_agent())
        agent = AgentInfo(name="clean-reviewer", path=path, scope="global")

        result = check_quality(agent)

        issue_types = {i["type"] for i in result["issues"]}
        assert "missing_ask_before_fallback" not in issue_types


# --- Constants tests ---


class TestConstants:
    """定数のテスト。"""

    def test_anti_patterns_not_empty(self):
        assert len(ANTI_PATTERNS) >= 5

    def test_best_practices_not_empty(self):
        assert len(BEST_PRACTICES) >= 4
