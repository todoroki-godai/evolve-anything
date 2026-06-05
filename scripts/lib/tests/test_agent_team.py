"""agent_team（エージェント編成ギャップ検出, Issue #326）のテスト。

決定論・LLM 非依存。役割重複（Jaccard）と孤立（被参照ゼロ）を検証する。
"""
from pathlib import Path

import agent_team
from agent_quality import AgentInfo


def _mk(name: str, description: str = "", content: str = "") -> AgentInfo:
    return AgentInfo(
        name=name,
        path=Path(f"/tmp/{name}.md"),
        scope="global",
        frontmatter={"description": description},
        content=content or description,
    )


# --- detect_role_overlaps ---------------------------------------------------


def test_role_overlap_detected_for_similar_descriptions():
    a = _mk("planner-a", "Plan implementation strategy and design architecture for backend services")
    b = _mk("planner-b", "Plan implementation strategy and design architecture for backend systems")
    overlaps = agent_team.detect_role_overlaps([a, b], threshold=0.5)
    assert len(overlaps) == 1
    assert {overlaps[0].agent_a, overlaps[0].agent_b} == {"planner-a", "planner-b"}
    assert overlaps[0].similarity >= 0.5


def test_no_role_overlap_for_distinct_descriptions():
    a = _mk("painter", "Generate watercolor landscape illustrations for greeting cards")
    b = _mk("dba", "Optimize PostgreSQL query plans and tune database indexes")
    assert agent_team.detect_role_overlaps([a, b], threshold=0.5) == []


def test_role_overlap_ignores_examples_block():
    # description 本体は全く異なるが Examples 定型句が共通でもノイズで誤検出しない
    a = _mk(
        "alpha",
        "Audit cloud cost and propose savings.\n\nExamples:\n- User: 「相談して」\n  Assistant: \"はい\"",
    )
    b = _mk(
        "beta",
        "Translate legal documents between languages.\n\nExamples:\n- User: 「相談して」\n  Assistant: \"はい\"",
    )
    assert agent_team.detect_role_overlaps([a, b], threshold=0.5) == []


# --- detect_isolated --------------------------------------------------------


def test_isolated_agent_when_unreferenced():
    router = _mk("router", "Routes requests", content="delegate to worker-a or worker-b")
    a = _mk("worker-a", "Does A")
    b = _mk("worker-b", "Does B")
    lonely = _mk("worker-c", "Does C")  # router 本文から未参照
    isolated = agent_team.detect_isolated([router, a, b, lonely])
    assert isolated == ["worker-c"]


def test_not_isolated_when_referenced_by_other_agent():
    router = _mk("router", "Routes", content="use worker-a when needed")
    a = _mk("worker-a", "Does A")
    assert agent_team.detect_isolated([router, a]) == []


# --- analyze_agent_team -----------------------------------------------------


def test_analyze_sets_has_gap_true_on_overlap():
    a = _mk("planner-a", "Plan implementation strategy and design architecture", content="x")
    b = _mk("planner-b", "Plan implementation strategy and design architecture", content="planner-a planner-b")
    result = agent_team.analyze_agent_team([a, b])
    assert result.total_agents == 2
    assert result.has_gap is True


def test_analyze_clean_team_has_no_gap():
    a = _mk("painter", "Generate illustrations", content="works with dba")
    b = _mk("dba", "Tune databases", content="works with painter")
    result = agent_team.analyze_agent_team([a, b])
    assert result.has_gap is False
    assert result.role_overlaps == []
    assert result.isolated == []


# --- build_agent_team_section (observability builder) -----------------------


def test_builder_returns_none_when_fewer_than_two_agents(monkeypatch):
    from audit import sections_agent

    monkeypatch.setattr(sections_agent, "scan_agents", lambda **kw: [_mk("solo", "only one")])
    assert sections_agent.build_agent_team_section(Path("/tmp")) is None


def test_builder_emits_clean_marker_when_no_gap(monkeypatch):
    from audit import sections_agent

    agents = [
        _mk("painter", "Generate illustrations", content="works with dba"),
        _mk("dba", "Tune databases", content="works with painter"),
    ]
    monkeypatch.setattr(sections_agent, "scan_agents", lambda **kw: agents)
    section = sections_agent.build_agent_team_section(Path("/tmp"))
    assert section is not None
    joined = "\n".join(section)
    assert "✓" in joined and "編成ギャップなし" in joined


def test_builder_flags_overlap_and_isolated(monkeypatch):
    from audit import sections_agent

    agents = [
        _mk("planner-a", "Plan implementation strategy and design architecture", content="x"),
        _mk("planner-b", "Plan implementation strategy and design architecture", content="x"),
        _mk("lonely", "Does unique work nobody references", content="x"),
    ]
    monkeypatch.setattr(sections_agent, "scan_agents", lambda **kw: agents)
    section = sections_agent.build_agent_team_section(Path("/tmp"))
    assert section is not None
    joined = "\n".join(section)
    assert "⚠" in joined
    assert "役割重複" in joined
    assert "孤立" in joined
    assert "lonely" in joined
