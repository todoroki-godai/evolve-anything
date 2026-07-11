"""agent tier 適合ゲートの audit advisory section のテスト。

`check_agent_tier`（純関数）を全 agent に適用し、モデルティア不適合を audit に
surface する。決定論・LLM 非依存・advisory のみ（スコア重み非関与）。

scan_agents はグローバル ~/.claude/agents も走査するため、テストは section 側の
`scan_agents` を monkeypatch して合成 AgentInfo を注入する（hermetic）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from agent_quality import AgentInfo  # noqa: E402
from audit import sections_agent_tier as sat  # noqa: E402
from audit.sections_agent_tier import build_agent_tier_section  # noqa: E402


def _agent(name, frontmatter):
    return AgentInfo(name=name, path=Path(f"/tmp/{name}.md"), scope="global",
                     frontmatter=frontmatter)


def _patch(monkeypatch, agents, env=None):
    monkeypatch.setattr(sat, "scan_agents", lambda project_root=None: agents)
    monkeypatch.setattr(
        sat, "check_subagent_model_env_override",
        lambda: sat._orig_env_check(env=env or {}),
    )


def test_none_when_no_agents_and_no_env(tmp_path, monkeypatch):
    _patch(monkeypatch, [])
    assert build_agent_tier_section(tmp_path) is None


def test_clean_when_all_compliant(tmp_path, monkeypatch):
    agents = [
        _agent("h", {"name": "h", "tier": "HEAD", "model": "sonnet", "effort": "max"}),
        _agent("m", {"name": "m", "tier": "MECH", "model": "haiku"}),
    ]
    _patch(monkeypatch, agents)
    section = build_agent_tier_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "✓" in combined
    assert section[0].startswith("## ")


def test_mismatch_surfaced_as_warning(tmp_path, monkeypatch):
    agents = [
        _agent("r", {"name": "r", "tier": "REVIEW", "model": "opus", "effort": "high"}),
    ]
    _patch(monkeypatch, agents)
    section = build_agent_tier_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "r" in combined
    assert "tier_model_mismatch" in combined or "fable" in combined


def test_missing_tier_shown_as_info_count(tmp_path, monkeypatch):
    agents = [
        _agent("a", {"name": "a", "model": "opus"}),
        _agent("b", {"name": "b", "model": "sonnet"}),
    ]
    _patch(monkeypatch, agents)
    section = build_agent_tier_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "ℹ" in combined
    assert "2" in combined  # 2 エージェントに tier 宣言なし


def test_exact_id_pin_surfaced(tmp_path, monkeypatch):
    agents = [
        _agent("h", {"name": "h", "tier": "HEAD", "model": "claude-opus-4-8",
                     "effort": "max"}),
    ]
    _patch(monkeypatch, agents)
    section = build_agent_tier_section(tmp_path)
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "exact_id_pin" in combined or "claude-opus-4-8" in combined


def test_env_override_surfaced(tmp_path, monkeypatch):
    agents = [
        _agent("h", {"name": "h", "tier": "HEAD", "model": "sonnet", "effort": "max"}),
    ]
    _patch(monkeypatch, agents, env={"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"})
    section = build_agent_tier_section(tmp_path)
    combined = "\n".join(section)
    assert "CLAUDE_CODE_SUBAGENT_MODEL" in combined


def test_section_is_list_of_str(tmp_path, monkeypatch):
    agents = [_agent("r", {"name": "r", "tier": "REVIEW", "model": "opus"})]
    _patch(monkeypatch, agents)
    section = build_agent_tier_section(tmp_path)
    assert isinstance(section, list)
    assert all(isinstance(x, str) for x in section)


def test_registered_in_observability_builders():
    from audit.observability import _OBSERVABILITY_BUILDERS
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "agent_tier" in keys
