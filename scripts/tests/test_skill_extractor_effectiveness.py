"""skill_extractor.effectiveness のユニットテスト（issue #306）。

軌跡有効性の実証特徴量（多様性・反復性・成功/失敗コントラスト, arXiv:2606.03461）が
generalizability_score の算定に反映されていることを検証する。

TDD-first・決定論・LLM 非依存。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from skill_extractor.trajectory_sampler import TrajectoryRecord
from skill_extractor.effectiveness import (
    compute_effectiveness,
    compute_diversity,
    compute_recurrence,
    compute_contrast,
    effectiveness_multiplier,
    MIN_MULTIPLIER,
    CONTRAST_BOTH,
    CONTRAST_ALL_SUCCESS,
    CONTRAST_ALL_FAILURE,
)
from skill_extractor.skill_extractor import (
    _compute_generalizability_score,
    extract_skill_candidates,
)


def _rec(skill="sk", prompt="p", outcome="success", session="s"):
    return TrajectoryRecord(
        skill_name=skill,
        user_prompt=prompt,
        outcome=outcome,
        session_id=session,
        timestamp="t",
    )


# ── compute_diversity ────────────────────────────────────


class TestDiversity:
    def test_all_distinct_prompts_max(self):
        recs = [_rec(prompt=f"p{i}") for i in range(5)]
        assert compute_diversity(recs) == 1.0

    def test_all_same_prompt_low(self):
        recs = [_rec(prompt="same") for _ in range(5)]
        assert compute_diversity(recs) == pytest.approx(0.2)

    def test_empty_prompts_neutral(self):
        recs = [_rec(prompt="") for _ in range(3)]
        assert compute_diversity(recs) == 0.5

    def test_range(self):
        recs = [_rec(prompt="a"), _rec(prompt="b"), _rec(prompt="a")]
        d = compute_diversity(recs)
        assert 0.0 <= d <= 1.0


# ── compute_recurrence ───────────────────────────────────


class TestRecurrence:
    def test_all_distinct_sessions_max(self):
        recs = [_rec(session=f"s{i}") for i in range(4)]
        assert compute_recurrence(recs) == 1.0

    def test_all_same_session_min(self):
        recs = [_rec(session="s1") for _ in range(4)]
        assert compute_recurrence(recs) == 0.0

    def test_single_record_neutral(self):
        assert compute_recurrence([_rec()]) == 0.5

    def test_empty_sessions_neutral(self):
        recs = [_rec(session="") for _ in range(3)]
        assert compute_recurrence(recs) == 0.5

    def test_partial_spread(self):
        # 2 distinct sessions out of 4 records: (2-1)/(4-1) = 1/3
        recs = [_rec(session="s1"), _rec(session="s1"), _rec(session="s2"), _rec(session="s2")]
        assert compute_recurrence(recs) == pytest.approx(1 / 3)


# ── compute_contrast ─────────────────────────────────────


class TestContrast:
    def test_both_success_and_failure(self):
        recs = [_rec(outcome="success"), _rec(outcome="failure")]
        assert compute_contrast(recs) == CONTRAST_BOTH

    def test_all_success(self):
        recs = [_rec(outcome="success") for _ in range(3)]
        assert compute_contrast(recs) == CONTRAST_ALL_SUCCESS

    def test_all_failure(self):
        recs = [_rec(outcome="failure") for _ in range(3)]
        assert compute_contrast(recs) == CONTRAST_ALL_FAILURE

    def test_only_unknown_neutral(self):
        recs = [_rec(outcome="unknown") for _ in range(3)]
        assert compute_contrast(recs) == 0.5


# ── compute_effectiveness / multiplier ───────────────────


class TestEffectiveness:
    def test_empty_returns_zero(self):
        assert compute_effectiveness([]) == 0.0

    def test_range(self):
        recs = [_rec(prompt=f"p{i}", session=f"s{i}", outcome="success") for i in range(5)]
        assert 0.0 <= compute_effectiveness(recs) <= 1.0

    def test_diverse_recurring_contrasted_beats_monotonous(self):
        good = [
            _rec(prompt="a", session="s1", outcome="success"),
            _rec(prompt="b", session="s2", outcome="failure"),
            _rec(prompt="c", session="s3", outcome="success"),
        ]
        bad = [
            _rec(prompt="same", session="s1", outcome="success"),
            _rec(prompt="same", session="s1", outcome="success"),
            _rec(prompt="same", session="s1", outcome="success"),
        ]
        assert compute_effectiveness(good) > compute_effectiveness(bad)

    def test_multiplier_empty_is_neutral(self):
        assert effectiveness_multiplier([]) == 1.0

    def test_multiplier_bounds(self):
        recs = [_rec(prompt="same", session="s1", outcome="unknown") for _ in range(3)]
        m = effectiveness_multiplier(recs)
        assert MIN_MULTIPLIER <= m <= 1.0

    def test_multiplier_high_effectiveness_near_one(self):
        recs = [
            _rec(prompt="a", session="s1", outcome="success"),
            _rec(prompt="b", session="s2", outcome="failure"),
        ]
        assert effectiveness_multiplier(recs) > MIN_MULTIPLIER


# ── generalizability_score 統合 ──────────────────────────


class TestScoreIntegration:
    def test_effectiveness_lowers_monotonous_cluster_score(self):
        """同一プロンプト・同一セッションの連投はスコアが下がる。"""
        monotonous = [
            _rec(prompt="same", session="s1", outcome="success") for _ in range(8)
        ]
        diverse = [
            _rec(prompt=f"p{i}", session=f"s{i}", outcome="success") for i in range(8)
        ]
        score_mono = _compute_generalizability_score(monotonous)
        score_div = _compute_generalizability_score(diverse)
        assert score_div > score_mono

    def test_backward_compat_flag_disables_effectiveness(self):
        """use_effectiveness=False は従来式に一致する。"""
        recs = [_rec(prompt="same", session="s1", outcome="success") for _ in range(8)]
        legacy = _compute_generalizability_score(recs, use_effectiveness=False)
        new = _compute_generalizability_score(recs, use_effectiveness=True)
        # 単調軌跡では new <= legacy（割り引かれる）
        assert new <= legacy
        # legacy は effectiveness 補正なしの素のスコア
        assert 0.0 <= legacy <= 1.0

    def test_score_in_range(self):
        for recs in (
            [],
            [_rec()],
            [_rec(prompt=f"p{i}", session=f"s{i}") for i in range(50)],
        ):
            s = _compute_generalizability_score(recs)
            assert 0.0 <= s <= 1.0

    def test_candidates_include_effectiveness_field(self, tmp_path):
        import json

        pj = tmp_path / "projects" / "pj"
        pj.mkdir(parents=True)
        turns = [
            {"type": "user", "message": {"role": "user", "content": "やって"},
             "sessionId": "s1", "uuid": "u1", "timestamp": "t1"},
            {"type": "user", "message": {"role": "user",
             "content": "<command-name>/a:foo</command-name>"},
             "sessionId": "s1", "uuid": "u2", "timestamp": "t2"},
            {"type": "assistant", "message": {"role": "assistant", "content": "done"},
             "sessionId": "s1", "uuid": "u3", "timestamp": "t3"},
        ]
        (pj / "s.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))
        cands = extract_skill_candidates(projects_root=tmp_path / "projects", min_cluster_size=1)
        assert cands
        for c in cands:
            assert "effectiveness" in c
            assert 0.0 <= c["effectiveness"] <= 1.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
