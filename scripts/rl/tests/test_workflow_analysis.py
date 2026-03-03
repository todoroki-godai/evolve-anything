#!/usr/bin/env python3
"""workflow_analysis.py のユニットテスト"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from workflow_analysis import (
    compress_pattern,
    compute_stats,
    generate_fitness_output,
    generate_hints,
    load_workflows,
    workflow_key,
)


# --- テストデータ ---

def make_workflow(
    skill_name="opsx:apply",
    workflow_type="skill-driven",
    steps=None,
    team_name=None,
):
    """テスト用ワークフローを生成"""
    if steps is None:
        steps = [{"tool": "Agent:Explore", "timestamp": "2026-01-01T00:00:00Z"}]
    wf = {
        "workflow_id": "wf-test",
        "workflow_type": workflow_type,
        "skill_name": skill_name,
        "steps": steps,
        "step_count": len(steps),
    }
    if team_name:
        wf["team_name"] = team_name
    return wf


# --- compress_pattern テスト ---

class TestCompressPattern:
    def test_連続同一エージェントを圧縮(self):
        steps = [
            {"tool": "Agent:Explore"},
            {"tool": "Agent:Explore"},
            {"tool": "Agent:Explore"},
            {"tool": "Agent:Plan"},
        ]
        assert compress_pattern(steps) == "Explore \u2192 Plan"

    def test_単一エージェント(self):
        steps = [{"tool": "Agent:Explore"}, {"tool": "Agent:Explore"}]
        assert compress_pattern(steps) == "Explore"

    def test_複数遷移(self):
        steps = [
            {"tool": "Agent:Explore"},
            {"tool": "Agent:Plan"},
            {"tool": "Agent:general-purpose"},
        ]
        assert compress_pattern(steps) == "Explore \u2192 Plan \u2192 general-purpose"

    def test_空ステップ(self):
        assert compress_pattern([]) == ""


# --- workflow_key テスト ---

class TestWorkflowKey:
    def test_skill_driven(self):
        wf = make_workflow(skill_name="opsx:apply", workflow_type="skill-driven")
        assert workflow_key(wf) == "opsx:apply"

    def test_team_driven(self):
        wf = make_workflow(workflow_type="team-driven", team_name="my-team")
        assert workflow_key(wf) == "team:my-team"

    def test_agent_burst(self):
        wf = make_workflow(workflow_type="agent-burst")
        assert workflow_key(wf) == "(agent-burst)"


# --- compute_stats テスト ---

class TestComputeStats:
    def test_基本統計(self):
        steps_explore = [{"tool": "Agent:Explore"}, {"tool": "Agent:Explore"}]
        workflows = [
            make_workflow(steps=steps_explore) for _ in range(5)
        ]
        stats = compute_stats(workflows, min_workflows=3)
        assert "opsx:apply" in stats
        s = stats["opsx:apply"]
        assert s["workflow_count"] == 5
        assert s["consistency"] == 1.0  # all same pattern
        assert s["avg_steps"] == 2.0
        assert s["dominant_pattern"] == "Explore"

    def test_min_workflows_フィルタ(self):
        workflows = [make_workflow(skill_name="rare-skill") for _ in range(2)]
        workflows += [make_workflow(skill_name="common-skill") for _ in range(5)]
        stats = compute_stats(workflows, min_workflows=3)
        assert "rare-skill" not in stats
        assert "common-skill" in stats

    def test_複数パターンの一貫性(self):
        steps_a = [{"tool": "Agent:Explore"}]
        steps_b = [{"tool": "Agent:Plan"}]
        workflows = [make_workflow(steps=steps_a) for _ in range(3)]
        workflows += [make_workflow(steps=steps_b) for _ in range(2)]
        stats = compute_stats(workflows, min_workflows=3)
        s = stats["opsx:apply"]
        assert s["workflow_count"] == 5
        assert s["consistency"] == 0.6  # 3/5

    def test_team_drivenの統計(self):
        workflows = [
            make_workflow(
                workflow_type="team-driven",
                team_name="dev",
                steps=[{"tool": "Agent:Explore"}],
            )
            for _ in range(4)
        ]
        stats = compute_stats(workflows, min_workflows=3)
        assert "team:dev" in stats

    def test_agent_burstの統計(self):
        workflows = [
            make_workflow(
                workflow_type="agent-burst",
                steps=[{"tool": "Agent:general-purpose"}],
            )
            for _ in range(3)
        ]
        stats = compute_stats(workflows, min_workflows=3)
        assert "(agent-burst)" in stats


# --- load_workflows テスト ---

class TestLoadWorkflows:
    def test_存在しないファイル(self, capsys):
        result = load_workflows(Path("/nonexistent/path/workflows.jsonl"))
        assert result == []
        captured = capsys.readouterr()
        assert "警告" in captured.err

    def test_空ファイル(self, tmp_path, capsys):
        p = tmp_path / "workflows.jsonl"
        p.write_text("", encoding="utf-8")
        result = load_workflows(p)
        assert result == []
        captured = capsys.readouterr()
        assert "警告" in captured.err

    def test_正常読み込み(self, tmp_path):
        p = tmp_path / "workflows.jsonl"
        wf = make_workflow()
        p.write_text(json.dumps(wf) + "\n", encoding="utf-8")
        result = load_workflows(p)
        assert len(result) == 1
        assert result[0]["skill_name"] == "opsx:apply"


# --- generate_hints テスト ---

class TestGenerateHints:
    def test_高一貫性ヒント(self):
        stats = {
            "my-skill": {
                "workflow_count": 10,
                "abstract_patterns": {"Explore": 8},
                "consistency": 0.8,
                "avg_steps": 2.0,
                "step_std": 0.5,
                "dominant_pattern": "Explore",
            }
        }
        hints = generate_hints(stats)
        assert "my-skill" in hints
        assert "安定" in hints["my-skill"]

    def test_低一貫性ヒント(self):
        stats = {
            "my-skill": {
                "workflow_count": 10,
                "abstract_patterns": {"Explore": 3, "Plan": 3, "general-purpose": 4},
                "consistency": 0.3,
                "avg_steps": 3.0,
                "step_std": 2.0,
                "dominant_pattern": "general-purpose",
            }
        }
        hints = generate_hints(stats)
        assert "一貫性が低い" in hints["my-skill"]

    def test_中程度一貫性ヒント(self):
        stats = {
            "my-skill": {
                "workflow_count": 10,
                "abstract_patterns": {"Explore": 5, "Plan": 5},
                "consistency": 0.5,
                "avg_steps": 2.0,
                "step_std": 1.0,
                "dominant_pattern": "Explore",
            }
        }
        hints = generate_hints(stats)
        assert "一貫性が中程度" in hints["my-skill"]


# --- generate_fitness_output テスト ---

class TestGenerateFitnessOutput:
    def test_fitness出力構造(self):
        stats = {
            "opsx:apply": {
                "workflow_count": 40,
                "abstract_patterns": {"Explore": 19},
                "consistency": 0.475,
                "avg_steps": 3.1,
                "step_std": 3.2,
                "dominant_pattern": "Explore",
            }
        }
        output = generate_fitness_output(stats)
        assert "workflow_stats" in output
        ws = output["workflow_stats"]["opsx:apply"]
        assert ws["consistency"] == 0.475
        assert ws["avg_steps"] == 3.1
        assert ws["dominant_pattern"] == "Explore"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
