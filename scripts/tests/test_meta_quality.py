"""meta_quality.py のユニットテスト (PR-D1b / Issue #203)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from meta_quality import meta_quality_check, _jaccard_similarity


class TestJaccardSimilarity:
    def test_identical_strings(self):
        assert _jaccard_similarity("foo bar baz", "foo bar baz") == 1.0

    def test_no_overlap(self):
        assert _jaccard_similarity("aaa bbb", "ccc ddd") == 0.0

    def test_partial_overlap(self):
        # {"foo", "bar"} ∩ {"foo", "qux"} = {"foo"} → 1/3
        sim = _jaccard_similarity("foo bar", "foo qux")
        assert abs(sim - 1 / 3) < 0.001

    def test_empty_strings(self):
        assert _jaccard_similarity("", "") == 0.0

    def test_case_insensitive(self):
        assert _jaccard_similarity("Foo Bar", "foo bar") == 1.0


class TestMetaQualityCheck:
    # 1. 高頻度・重複なし → CREATE
    def test_high_reuse_no_duplicate_returns_create(self):
        result = meta_quality_check(
            skill_name="my-new-skill",
            skill_content="Deploy and configure applications",
            usage_stats={"trigger_count": 20, "session_count": 50},
            all_skills=["unrelated-skill", "another-skill"],
        )
        assert result["recommendation"] == "CREATE"
        assert result["duplicate_candidates"] == []
        assert result["low_reuse"] is False

    # 2. 低頻度・重複なし → CREATE（頻度だけでは SKIP しない）
    def test_low_reuse_no_duplicate_returns_create_not_skip(self):
        result = meta_quality_check(
            skill_name="rare-skill",
            skill_content="Rare operation for edge cases",
            usage_stats={"trigger_count": 1, "session_count": 100},
            all_skills=["unrelated-skill", "another-tool"],
        )
        assert result["recommendation"] == "CREATE"
        assert result["low_reuse"] is True
        assert result["duplicate_candidates"] == []

    # 3. Jaccard > 0.6 の既存スキルあり → REVIEW
    def test_duplicate_candidate_returns_review(self):
        result = meta_quality_check(
            skill_name="deploy skill",
            skill_content="Deploy application to cloud",
            usage_stats={"trigger_count": 10, "session_count": 50},
            all_skills=["deploy skill clone", "unrelated"],
        )
        # "deploy skill" vs "deploy skill clone" → tokens: {"deploy","skill"} vs {"deploy","skill","clone"}
        # intersection=2, union=3 → Jaccard=0.667 > 0.6
        assert result["recommendation"] == "REVIEW"
        assert len(result["duplicate_candidates"]) > 0

    # 4. 低頻度 AND Jaccard > 0.6 → SKIP
    def test_low_reuse_and_duplicate_returns_skip(self):
        result = meta_quality_check(
            skill_name="deploy skill",
            skill_content="Deploy application to cloud",
            usage_stats={"trigger_count": 1, "session_count": 100},
            all_skills=["deploy skill clone", "unrelated"],
        )
        assert result["recommendation"] == "SKIP"
        assert result["low_reuse"] is True
        assert len(result["duplicate_candidates"]) > 0

    # 5. 全スキルリスト空 → duplicate_candidates=[] で正常動作
    def test_empty_all_skills_no_duplicates(self):
        result = meta_quality_check(
            skill_name="my-skill",
            skill_content="Some skill content",
            usage_stats={"trigger_count": 5, "session_count": 20},
            all_skills=[],
        )
        assert result["duplicate_candidates"] == []
        assert result["recommendation"] == "CREATE"

    # 6a. usage_stats が空 → ZeroDivision なし
    def test_empty_usage_stats_no_error(self):
        result = meta_quality_check(
            skill_name="my-skill",
            skill_content="Some skill content",
            usage_stats={},
            all_skills=["other-skill"],
        )
        assert "recommendation" in result
        assert isinstance(result["reuse_rate"], float)

    # 6b. session_count=0 → ZeroDivision なし
    def test_zero_session_count_no_error(self):
        result = meta_quality_check(
            skill_name="my-skill",
            skill_content="Some skill content",
            usage_stats={"trigger_count": 5, "session_count": 0},
            all_skills=[],
        )
        assert "recommendation" in result
        assert isinstance(result["reuse_rate"], float)

    def test_result_schema(self):
        """返り値に必要なキーが全て揃っていること。"""
        result = meta_quality_check(
            skill_name="test",
            skill_content="test content",
            usage_stats={"trigger_count": 3, "session_count": 10},
            all_skills=["other"],
        )
        required_keys = {
            "skill_name",
            "reuse_rate",
            "low_reuse",
            "is_specialized",
            "duplicate_candidates",
            "recommendation",
            "reason",
        }
        assert required_keys.issubset(result.keys())

    def test_reuse_rate_calculation(self):
        result = meta_quality_check(
            skill_name="test",
            skill_content="test content",
            usage_stats={"trigger_count": 5, "session_count": 50},
            all_skills=[],
        )
        assert abs(result["reuse_rate"] - 0.1) < 0.001

    def test_low_reuse_threshold(self):
        """trigger_count / session_count < 0.1 → low_reuse=True."""
        result = meta_quality_check(
            skill_name="test",
            skill_content="test content",
            usage_stats={"trigger_count": 4, "session_count": 50},
            all_skills=[],
        )
        # 4/50 = 0.08 < 0.1 → low_reuse
        assert result["low_reuse"] is True

    def test_not_low_reuse_above_threshold(self):
        """trigger_count / session_count >= 0.1 → low_reuse=False."""
        result = meta_quality_check(
            skill_name="test",
            skill_content="test content",
            usage_stats={"trigger_count": 10, "session_count": 50},
            all_skills=[],
        )
        # 10/50 = 0.2 >= 0.1 → not low_reuse
        assert result["low_reuse"] is False

    def test_self_not_in_duplicate_candidates(self):
        """自分自身のスキル名は duplicate_candidates に含まれない。"""
        result = meta_quality_check(
            skill_name="deploy skill",
            skill_content="Deploy application",
            usage_stats={"trigger_count": 5, "session_count": 20},
            all_skills=["deploy skill"],  # 自分自身だけ
        )
        assert "deploy skill" not in result["duplicate_candidates"]
