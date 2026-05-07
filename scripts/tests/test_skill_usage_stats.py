"""skill_usage_stats.py のテスト。"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import skill_usage_stats as sus
from skill_usage_stats import find_nested_only_skills, find_merge_candidates


def _write_activations(tmp_path: Path, records: list) -> Path:
    f = tmp_path / "skill_activations.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return f


def _ts(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ── load_skill_activations ───────────────────────────────────────────────────

class TestLoadSkillActivations:
    def test_missing_file_returns_empty(self, tmp_path):
        result = sus.load_skill_activations(activations_file=tmp_path / "nonexistent.jsonl")
        assert result == {}

    def test_basic_aggregation(self, tmp_path):
        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "project": "p"},
            {"ts": _ts(2), "skill": "ship", "session_id": "s2", "project": "p"},
            {"ts": _ts(3), "skill": "review", "session_id": "s3", "project": "p"},
        ])
        stats = sus.load_skill_activations(days=30, activations_file=f)
        assert stats["ship"]["count"] == 2
        assert stats["review"]["count"] == 1

    def test_old_records_excluded(self, tmp_path):
        f = _write_activations(tmp_path, [
            {"ts": _ts(100), "skill": "ship", "session_id": "s1", "project": "p"},
            {"ts": _ts(1), "skill": "review", "session_id": "s2", "project": "p"},
        ])
        stats = sus.load_skill_activations(days=30, activations_file=f)
        assert "ship" not in stats
        assert "review" in stats

    def test_plugin_prefix_normalized(self, tmp_path):
        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "rl-anything:audit", "session_id": "s1", "project": "p"},
        ])
        stats = sus.load_skill_activations(days=30, activations_file=f)
        # both prefixed and base name are registered
        assert "rl-anything:audit" in stats
        assert "audit" in stats
        assert stats["audit"]["count"] == 1

    def test_malformed_lines_skipped(self, tmp_path):
        f = tmp_path / "skill_activations.jsonl"
        f.write_text(
            '{"ts": "' + _ts(1) + '", "skill": "ship", "session_id": "s1"}\n'
            "not-json\n"
            '{"ts": "' + _ts(1) + '", "skill": "review", "session_id": "s2"}\n'
        )
        stats = sus.load_skill_activations(days=30, activations_file=f)
        assert stats["ship"]["count"] == 1
        assert stats["review"]["count"] == 1

    def test_days_since_populated(self, tmp_path):
        f = _write_activations(tmp_path, [
            {"ts": _ts(5), "skill": "ship", "session_id": "s1", "project": "p"},
        ])
        stats = sus.load_skill_activations(days=30, activations_file=f)
        assert 4.5 < stats["ship"]["days_since"] < 5.5


# ── get_installed_global_skills ──────────────────────────────────────────────

class TestGetInstalledGlobalSkills:
    def test_returns_skills_with_skill_md(self, tmp_path):
        skills_dir = tmp_path / ".claude" / "skills"
        (skills_dir / "ship").mkdir(parents=True)
        (skills_dir / "ship" / "SKILL.md").touch()
        (skills_dir / "review").mkdir(parents=True)
        (skills_dir / "review" / "SKILL.md").touch()
        (skills_dir / "no-skill-md").mkdir(parents=True)  # skip

        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = sus.get_installed_global_skills()

        assert sorted(result) == ["review", "ship"]

    def test_missing_dir_returns_empty(self, tmp_path):
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", tmp_path / "nonexistent"):
            result = sus.get_installed_global_skills()
        assert result == []


# ── find_unused_global_skills ────────────────────────────────────────────────

class TestFindUnusedGlobalSkills:
    def test_no_activations_file_returns_empty(self, tmp_path):
        result = sus.find_unused_global_skills(activations_file=tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_unused_skill_detected(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "review", "audit"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = sus.find_unused_global_skills(days=30, activations_file=f)

        unused_names = {r["skill_name"] for r in result}
        assert unused_names == {"review", "audit"}
        assert "ship" not in unused_names

    def test_all_used_returns_empty(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "review"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "project": "p"},
            {"ts": _ts(2), "skill": "review", "session_id": "s2", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = sus.find_unused_global_skills(days=30, activations_file=f)
        assert result == []


# ── find_rarely_used_global_skills ───────────────────────────────────────────

class TestFindRarelyUsedGlobalSkills:
    def test_rarely_used_detected(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "review", "audit"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "project": "p"},
            {"ts": _ts(2), "skill": "ship", "session_id": "s2", "project": "p"},
            {"ts": _ts(1), "skill": "review", "session_id": "s3", "project": "p"},
            # audit: 0 invocations → unused (not rarely)
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = sus.find_rarely_used_global_skills(days=30, threshold=3, activations_file=f)

        names = {r["skill_name"] for r in result}
        assert "review" in names   # count=1 < threshold=3
        assert "ship" in names     # count=2 < threshold=3
        assert "audit" not in names  # count=0 → unused, not rarely

    def test_threshold_boundary(self, tmp_path):
        skills_dir = tmp_path / "skills"
        (skills_dir / "ship").mkdir(parents=True)
        (skills_dir / "ship" / "SKILL.md").touch()

        # exactly threshold → not included (threshold=exclusive upper bound)
        f = _write_activations(tmp_path, [
            {"ts": _ts(i), "skill": "ship", "session_id": f"s{i}", "project": "p"}
            for i in range(3)
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = sus.find_rarely_used_global_skills(days=30, threshold=3, activations_file=f)
        assert result == []  # count=3, threshold=3 → excluded


# ── find_nested_only_skills ──────────────────────────────────────────────────

class TestFindNestedOnlySkills:
    def test_nested_only_detected(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "helper", "review"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "invocation_trigger": "top-level", "project": "p"},
            {"ts": _ts(1), "skill": "helper", "session_id": "s2", "invocation_trigger": "nested-skill", "project": "p"},
            {"ts": _ts(2), "skill": "helper", "session_id": "s3", "invocation_trigger": "nested-skill", "project": "p"},
            {"ts": _ts(1), "skill": "review", "session_id": "s4", "invocation_trigger": "top-level", "project": "p"},
            {"ts": _ts(2), "skill": "review", "session_id": "s5", "invocation_trigger": "nested-skill", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = find_nested_only_skills(days=30, activations_file=f)

        names = {r["skill_name"] for r in result}
        assert "helper" in names      # nested only
        assert "ship" not in names    # top-level only
        assert "review" not in names  # both top-level and nested

    def test_nested_count_populated(self, tmp_path):
        skills_dir = tmp_path / "skills"
        (skills_dir / "helper").mkdir(parents=True)
        (skills_dir / "helper" / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(i), "skill": "helper", "session_id": f"s{i}", "invocation_trigger": "nested-skill", "project": "p"}
            for i in range(5)
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = find_nested_only_skills(days=30, activations_file=f)

        assert result[0]["nested_count"] == 5

    def test_no_data_returns_empty(self, tmp_path):
        result = find_nested_only_skills(activations_file=tmp_path / "nonexistent.jsonl")
        assert result == []


# ── top_level_count / nested_count in load_skill_activations ─────────────────

class TestInvocationTriggerCounting:
    def test_counts_split_correctly(self, tmp_path):
        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "invocation_trigger": "top-level", "project": "p"},
            {"ts": _ts(1), "skill": "ship", "session_id": "s2", "invocation_trigger": "nested-skill", "project": "p"},
            {"ts": _ts(1), "skill": "ship", "session_id": "s3", "invocation_trigger": "top-level", "project": "p"},
        ])
        stats = sus.load_skill_activations(days=30, activations_file=f)
        assert stats["ship"]["count"] == 3
        assert stats["ship"]["top_level_count"] == 2
        assert stats["ship"]["nested_count"] == 1


# ── find_merge_candidates ────────────────────────────────────────────────────

class TestFindMergeCandidates:
    def test_single_caller_high_confidence(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "helper"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "invocation_trigger": "top-level", "parent_skill": None, "project": "p"},
            {"ts": _ts(1), "skill": "helper", "session_id": "s2", "invocation_trigger": "nested-skill", "parent_skill": "ship", "project": "p"},
            {"ts": _ts(2), "skill": "helper", "session_id": "s3", "invocation_trigger": "nested-skill", "parent_skill": "ship", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = find_merge_candidates(days=30, activations_file=f)

        assert len(result) == 1
        assert result[0]["skill_name"] == "helper"
        assert result[0]["merge_into"] == "ship"
        assert result[0]["confidence"] == "high"

    def test_multiple_callers_low_confidence(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "review", "helper"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "helper", "session_id": "s1", "invocation_trigger": "nested-skill", "parent_skill": "ship", "project": "p"},
            {"ts": _ts(2), "skill": "helper", "session_id": "s2", "invocation_trigger": "nested-skill", "parent_skill": "review", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = find_merge_candidates(days=30, activations_file=f)

        assert result[0]["confidence"] == "low"
        assert result[0]["merge_into"] in ("ship", "review")

    def test_no_callers_unknown_confidence(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["helper"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        # parent_skill なし（旧データ）
        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "helper", "session_id": "s1", "invocation_trigger": "nested-skill", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = find_merge_candidates(days=30, activations_file=f)

        assert result[0]["confidence"] == "unknown"
        assert result[0]["merge_into"] is None

    def test_callers_field_populated(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["helper"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(i), "skill": "helper", "session_id": f"s{i}", "invocation_trigger": "nested-skill", "parent_skill": "ship", "project": "p"}
            for i in range(3)
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = find_merge_candidates(days=30, activations_file=f)

        assert result[0]["callers"] == {"ship": 3}


# ── get_skill_activation_summary ─────────────────────────────────────────────

class TestGetSkillActivationSummary:
    def test_no_data_has_data_false(self, tmp_path):
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", tmp_path / "skills"):
            result = sus.get_skill_activation_summary(
                activations_file=tmp_path / "nonexistent.jsonl"
            )
        assert result["has_data"] is False
        assert result["unused_count"] == 0

    def test_summary_structure(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["ship", "review", "helper"]:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").touch()

        f = _write_activations(tmp_path, [
            {"ts": _ts(1), "skill": "ship", "session_id": "s1", "invocation_trigger": "top-level", "project": "p"},
            {"ts": _ts(1), "skill": "helper", "session_id": "s2", "invocation_trigger": "nested-skill", "project": "p"},
        ])
        with mock.patch.object(sus, "GLOBAL_SKILLS_DIR", skills_dir):
            result = sus.get_skill_activation_summary(days=30, activations_file=f)

        assert result["has_data"] is True
        assert result["total_installed"] == 3
        assert result["unused_count"] == 1          # review
        assert result["nested_only_count"] == 1     # helper
        assert result["unused"][0]["skill_name"] == "review"
