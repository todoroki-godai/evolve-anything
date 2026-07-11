#!/usr/bin/env python3
"""tier_policy_sync.py のテスト — sync エンジン（agent/settings/routing_rule targets, #193）。

決定論・LLM 非依存。plan_sync は read-only（byte 比較で書込ゼロを保証）、
apply_sync は drift のみ書き込み、直後の再 plan_sync が全 in_sync（冪等）であることを検証する。
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import tier_policy  # noqa: E402
import tier_policy_sync as sync  # noqa: E402

_TIERS = {t: dict(p) for t, p in tier_policy.DEFAULT_TIER_POLICY.items()}


def _agent_text(tier, model, effort, body="\nAgent body here.\n"):
    lines = ["---", "name: a", f"tier: {tier}"]
    if model is not None:
        lines.append(f"model: {model}")
    if effort is not None:
        lines.append(f"effort: {effort}")
    lines.append("---")
    return "\n".join(lines) + body


# --- desired_agent_text（純関数）---------------------------------------------


class TestDesiredAgentText:
    def test_replaces_existing_model_and_effort(self):
        text = _agent_text("HEAD", "opus", "xhigh")
        desired = sync.desired_agent_text(text, "sonnet", "max")
        assert "model: sonnet" in desired
        assert "effort: max" in desired
        assert "model: opus" not in desired
        assert "effort: xhigh" not in desired

    def test_inserts_missing_model_and_effort(self):
        text = "---\nname: a\ntier: HEAD\n---\nBody.\n"
        desired = sync.desired_agent_text(text, "sonnet", "max")
        assert "model: sonnet" in desired
        assert "effort: max" in desired

    def test_removes_effort_line_when_target_effort_none(self):
        text = _agent_text("MECH", "haiku", "high")
        desired = sync.desired_agent_text(text, "haiku", None)
        assert "effort:" not in desired
        assert "model: haiku" in desired

    def test_body_is_byte_exact(self):
        body = "\n\nSome body **with markdown**.\n\n- list item\n"
        text = _agent_text("HEAD", "opus", "xhigh", body=body)
        desired = sync.desired_agent_text(text, "sonnet", "max")
        assert desired.endswith(body)

    def test_no_frontmatter_returns_none(self):
        assert sync.desired_agent_text("no frontmatter here", "sonnet", "max") is None

    def test_unterminated_frontmatter_returns_none(self):
        assert sync.desired_agent_text("---\nname: a\n", "sonnet", "max") is None

    def test_idempotent_when_already_desired(self):
        text = _agent_text("HEAD", "sonnet", "max")
        desired = sync.desired_agent_text(text, "sonnet", "max")
        assert desired == text


# --- desired_settings_text ----------------------------------------------------


class TestDesiredSettingsText:
    def test_preserves_other_keys_and_indent(self):
        text = json.dumps({"a": 1, "model": "opus", "b": [1, 2]}, indent=2) + "\n"
        desired = sync.desired_settings_text(text, "sonnet", "max")
        data = json.loads(desired)
        assert data["a"] == 1
        assert data["b"] == [1, 2]
        assert data["model"] == "sonnet"
        assert data["effortLevel"] == "max"
        assert desired.endswith("\n")
        # 2 スペースインデント確認
        assert '"a": 1' in desired

    def test_removes_effort_level_when_target_effort_none(self):
        text = json.dumps({"model": "sonnet", "effortLevel": "high"}, indent=2) + "\n"
        desired = sync.desired_settings_text(text, "haiku", None)
        data = json.loads(desired)
        assert "effortLevel" not in data
        assert data["model"] == "haiku"

    def test_invalid_json_returns_none(self):
        assert sync.desired_settings_text("{ not json", "sonnet", "max") is None

    def test_no_trailing_newline_preserved(self):
        text = json.dumps({"model": "opus"}, indent=2)  # no trailing \n
        desired = sync.desired_settings_text(text, "sonnet", "max")
        assert not desired.endswith("\n\n")
        assert not desired.endswith("\n")


# --- render_routing_line / desired_routing_text -------------------------------


class TestRoutingLine:
    def test_render_includes_all_tiers_in_order(self):
        line = sync.render_routing_line(_TIERS)
        assert line.index("HEAD=") < line.index("HARD=") < line.index("NORMAL=") \
            < line.index("MECH=") < line.index("REVIEW=")

    def test_effort_none_tier_has_no_effort_segment(self):
        line = sync.render_routing_line(_TIERS)
        mech_segment = line.split("MECH=", 1)[1].split(" / ")[0]
        # description 文言自体に "effort" が含まれ得るため、値付きセグメント
        # マーカー "・effort " の非存在で判定する（naive substring は誤検出する）。
        assert "・effort " not in mech_segment
        assert mech_segment.startswith("haiku（")

    def test_effort_present_tier_has_effort_segment(self):
        line = sync.render_routing_line(_TIERS)
        head_segment = line.split("HEAD=", 1)[1].split(" / ")[0]
        assert "effort max" in head_segment

    def test_desired_routing_text_replaces_between_markers(self):
        text = (
            "# rule\n"
            "<!-- evolve-tier:begin -->\n"
            "- old stale line\n"
            "<!-- evolve-tier:end -->\n"
            "tail content\n"
        )
        desired = sync.desired_routing_text(text, _TIERS)
        assert "old stale line" not in desired
        assert "tail content" in desired
        assert "<!-- evolve-tier:begin -->" in desired
        assert "<!-- evolve-tier:end -->" in desired
        assert "HEAD=sonnet" in desired

    def test_missing_marker_returns_none(self):
        text = "# rule\nno markers here\n"
        assert sync.desired_routing_text(text, _TIERS) is None

    def test_idempotent_when_already_synced(self):
        text = (
            "<!-- evolve-tier:begin -->\n"
            f"{sync.render_routing_line(_TIERS)}\n"
            "<!-- evolve-tier:end -->\n"
        )
        desired = sync.desired_routing_text(text, _TIERS)
        assert desired == text


# --- plan_sync / apply_sync（統合）--------------------------------------------


def _config(tmp_path, *, agents=None, settings=None, routing_rules=None, tiers=None):
    return {
        "tiers": tiers or _TIERS,
        "targets": {
            "agents": agents or [],
            "settings": settings or [],
            "routing_rules": routing_rules or [],
        },
    }


class TestPlanSyncAgent:
    def test_drift_when_mismatched(self, tmp_path):
        agent = tmp_path / "a.md"
        agent.write_text(_agent_text("HEAD", "opus", "xhigh"), encoding="utf-8")
        config = _config(tmp_path, agents=[str(agent)])
        plans = sync.plan_sync(config)
        assert len(plans) == 1
        assert plans[0]["status"] == "drift"
        assert plans[0]["type"] == "agent"
        assert plans[0]["diff"]

    def test_in_sync_when_matching(self, tmp_path):
        agent = tmp_path / "a.md"
        agent.write_text(_agent_text("HEAD", "sonnet", "max"), encoding="utf-8")
        config = _config(tmp_path, agents=[str(agent)])
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "in_sync"
        assert plans[0]["diff"] is None

    def test_skip_when_tier_absent(self, tmp_path):
        agent = tmp_path / "a.md"
        agent.write_text("---\nname: a\nmodel: sonnet\n---\nBody\n", encoding="utf-8")
        config = _config(tmp_path, agents=[str(agent)])
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "skip"

    def test_missing_when_file_absent(self, tmp_path):
        config = _config(tmp_path, agents=[str(tmp_path / "nope.md")])
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "missing"

    def test_plan_sync_never_writes(self, tmp_path):
        agent = tmp_path / "a.md"
        original = _agent_text("HEAD", "opus", "xhigh")
        agent.write_text(original, encoding="utf-8")
        config = _config(tmp_path, agents=[str(agent)])
        sync.plan_sync(config)
        assert agent.read_text(encoding="utf-8") == original


class TestPlanSyncSettings:
    def test_drift_and_apply(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps({"other": True, "model": "opus", "effortLevel": "xhigh"}, indent=2) + "\n",
            encoding="utf-8",
        )
        config = _config(
            tmp_path, settings=[{"path": str(settings_path), "tier": "HEAD"}]
        )
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "drift"
        assert plans[0]["type"] == "settings"

        results = sync.apply_sync(config)
        assert results[0]["applied"] is True
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data["model"] == "sonnet"
        assert data["effortLevel"] == "max"
        assert data["other"] is True

    def test_unknown_tier_skips(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"model": "opus"}, indent=2) + "\n", encoding="utf-8")
        config = _config(
            tmp_path, settings=[{"path": str(settings_path), "tier": "NOPE"}]
        )
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "skip"


class TestPlanSyncRoutingRule:
    def test_drift_and_apply(self, tmp_path):
        rule_path = tmp_path / "model-routing.md"
        rule_path.write_text(
            "# rule\n<!-- evolve-tier:begin -->\n- stale\n<!-- evolve-tier:end -->\ntail\n",
            encoding="utf-8",
        )
        config = _config(tmp_path, routing_rules=[str(rule_path)])
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "drift"
        assert plans[0]["type"] == "routing_rule"

        results = sync.apply_sync(config)
        assert results[0]["applied"] is True
        new_text = rule_path.read_text(encoding="utf-8")
        assert "stale" not in new_text
        assert "tail" in new_text

    def test_missing_marker_skips(self, tmp_path):
        rule_path = tmp_path / "model-routing.md"
        rule_path.write_text("# rule\nno markers\n", encoding="utf-8")
        config = _config(tmp_path, routing_rules=[str(rule_path)])
        plans = sync.plan_sync(config)
        assert plans[0]["status"] == "skip"


class TestApplySyncIdempotency:
    def test_apply_then_replan_all_in_sync(self, tmp_path):
        agent = tmp_path / "a.md"
        agent.write_text(_agent_text("HARD", "opus", "high"), encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps({"model": "opus", "effortLevel": "high"}, indent=2) + "\n",
            encoding="utf-8",
        )
        rule_path = tmp_path / "model-routing.md"
        rule_path.write_text(
            "<!-- evolve-tier:begin -->\n- stale\n<!-- evolve-tier:end -->\n",
            encoding="utf-8",
        )
        config = _config(
            tmp_path,
            agents=[str(agent)],
            settings=[{"path": str(settings_path), "tier": "HARD"}],
            routing_rules=[str(rule_path)],
        )
        first_plan = sync.plan_sync(config)
        assert {p["status"] for p in first_plan} == {"drift"}

        sync.apply_sync(config)

        second_plan = sync.plan_sync(config)
        assert {p["status"] for p in second_plan} == {"in_sync"}

    def test_apply_sync_rejects_list_input(self):
        with pytest.raises(TypeError):
            sync.apply_sync([{"path": "x", "type": "agent", "status": "drift"}])
