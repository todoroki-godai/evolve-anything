"""loop_ablation_stats のテスト（#234 PR3: 設計文脈 vs naive 生成 較正実験の統計コア）。

純粋関数のみ・LLM 非依存。mock 不要。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import loop_ablation_stats as las  # noqa: E402

EPSILON = 0.05


class TestAssessComparability:
    def test_identical_prompts_not_comparable(self):
        prompt = "同一プロンプトです。" * 5
        result = las.assess_comparability(prompt, prompt, corrections=[], context={})
        assert result["comparable"] is False
        assert result["prompt_diff_chars"] == 0
        assert result["reason"]

    def test_diff_below_floor_not_comparable(self):
        designed = "prompt base text here"
        naive = designed + "x" * (las.MIN_PROMPT_DIFF_CHARS - 1)
        result = las.assess_comparability(designed, naive, corrections=[], context={})
        assert result["comparable"] is False

    def test_diff_at_or_above_floor_is_comparable(self):
        designed = "prompt base text here"
        naive = designed[: len(designed) - las.MIN_PROMPT_DIFF_CHARS] if len(designed) > las.MIN_PROMPT_DIFF_CHARS else ""
        # 単純に MIN_PROMPT_DIFF_CHARS 文字以上長い naive プロンプトを作る
        naive = designed + "y" * las.MIN_PROMPT_DIFF_CHARS
        result = las.assess_comparability(designed, naive, corrections=[{"message": "x"}], context={})
        assert result["comparable"] is True
        assert result["prompt_diff_chars"] >= las.MIN_PROMPT_DIFF_CHARS

    def test_corrections_count_reflected(self):
        designed = "a" * 100
        naive = "a" * 10
        corrections = [{"message": "1"}, {"message": "2"}]
        result = las.assess_comparability(designed, naive, corrections=corrections, context={})
        assert result["corrections_count"] == 2

    def test_context_signals_listed(self):
        designed = "a" * 100
        naive = "a" * 10
        context = {"workflow_hint": "hint", "audit_issues": [1, 2], "pitfalls": "text", "unrelated_key": "x"}
        result = las.assess_comparability(designed, naive, corrections=[], context=context)
        assert set(result["context_signals"]) == {"workflow_hint", "audit_issues", "pitfalls"}

    def test_reason_none_when_comparable(self):
        designed = "a" * 100
        naive = "a" * 10
        result = las.assess_comparability(designed, naive, corrections=[], context={})
        assert result["reason"] is None


def _scores(mean_values):
    """{axis: [values]} を作るヘルパー。全軸同一値リストを使う。"""
    return {axis: list(mean_values) for axis in las.AXES}


class TestCompareAblationScores:
    def test_designed_clear_win(self):
        designed = _scores([0.90, 0.91, 0.89])
        naive = _scores([0.50, 0.51, 0.49])
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["verdict"] == "designed_wins"
        assert result["naive_wins_warning"] is False
        for axis in las.AXES:
            assert result["axes"][axis]["verdict"] == "designed_wins"

    def test_delta_below_epsilon_is_inconclusive(self):
        designed = _scores([0.71, 0.70, 0.72])
        naive = _scores([0.70, 0.69, 0.71])
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["verdict"] == "inconclusive"

    def test_wide_variance_overlapping_bands_inconclusive(self):
        # mean delta (0.2) > epsilon だが std が大きく帯が重なる
        designed = _scores([0.9, 0.5, 0.7])
        naive = _scores([0.6, 0.5, 0.4])
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["axes"]["integrated"]["overlap"] is True
        assert result["verdict"] == "inconclusive"

    def test_naive_reversal_flags_warning(self):
        designed = _scores([0.40, 0.41, 0.39])
        naive = _scores([0.80, 0.81, 0.79])
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["verdict"] == "naive_wins"
        assert result["naive_wins_warning"] is True

    def test_axis_breakdown_is_independent_per_axis(self):
        designed = {
            "technical": [0.90, 0.91, 0.89],
            "domain": [0.40, 0.41, 0.39],
            "structure": [0.70, 0.70, 0.70],
            "integrated": [0.90, 0.91, 0.89],
        }
        naive = {
            "technical": [0.50, 0.51, 0.49],
            "domain": [0.80, 0.81, 0.79],
            "structure": [0.70, 0.69, 0.71],
            "integrated": [0.50, 0.51, 0.49],
        }
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["axes"]["technical"]["verdict"] == "designed_wins"
        assert result["axes"]["domain"]["verdict"] == "naive_wins"
        assert result["axes"]["structure"]["verdict"] == "inconclusive"
        # 全体 verdict は integrated 軸に基づく
        assert result["verdict"] == "designed_wins"

    def test_low_sample_size_caveat_true_for_n3(self):
        designed = _scores([0.90, 0.91, 0.89])
        naive = _scores([0.50, 0.51, 0.49])
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["n"] == 3
        assert result["low_sample_size_caveat"] is True

    def test_low_sample_size_caveat_false_for_n5(self):
        designed = _scores([0.90, 0.91, 0.89, 0.90, 0.92])
        naive = _scores([0.50, 0.51, 0.49, 0.50, 0.52])
        result = las.compare_ablation_scores(designed, naive, epsilon=EPSILON)
        assert result["n"] == 5
        assert result["low_sample_size_caveat"] is False


class TestEstimateAblationCost:
    def test_monotonic_in_content_length(self):
        small = las.estimate_ablation_cost(content_length=500, n=3)
        large = las.estimate_ablation_cost(content_length=5000, n=3)
        assert large["est_total_tokens"] > small["est_total_tokens"]

    def test_monotonic_in_n(self):
        small_n = las.estimate_ablation_cost(content_length=1000, n=3)
        large_n = las.estimate_ablation_cost(content_length=1000, n=6)
        assert large_n["est_total_tokens"] > small_n["est_total_tokens"]

    def test_call_counts_8n(self):
        result = las.estimate_ablation_cost(content_length=1000, n=3)
        assert result["generation_calls"] == 6
        assert result["scoring_calls"] == 18
        assert result["total_calls"] == 24
