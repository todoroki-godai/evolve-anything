"""pipeline_eval.py のユニットテスト。

PipelineEvalRunner の3ケース:
  - test_eval_type1_pattern_extraction: eval_set を渡して EvalResult が返る
  - test_eval_type2_prompt_optimization: load_patterns をモックして EvalResult が返る
  - test_compare_report: 2つの EvalResult から ComparisonReport が生成される
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from pipeline_eval import (
    ComparisonReport,
    EvalResult,
    PipelineEvalRunner,
)


# ── フィクスチャ ──────────────────────────────────────────

@pytest.fixture
def runner():
    return PipelineEvalRunner()


@pytest.fixture
def eval_set_with_labels():
    """should_trigger / should_not_trigger が混在する eval_set。"""
    return [
        {"query": "CDKでLambdaをデプロイしたい", "should_trigger": True},
        {"query": "CDKのデプロイでエラーが出た", "should_trigger": True},
        {"query": "deployの状態を見たい", "should_trigger": True},
        {"query": "チャンネルの動画をダウンロードしたい", "should_trigger": False},
        {"query": "全く関係ない質問", "should_trigger": False},
    ]


@pytest.fixture
def patterns_two_entries():
    """evolution_memory.load_patterns が返すモックデータ（2件）。"""
    return [
        {
            "ts": "2026-05-10T10:00:00+00:00",
            "skill_name": "aws-cdk-deploy",
            "strategy": "llm_improve",
            "score_before": 0.6,
            "score_after": 0.8,
            "patch_summary": "trigger wordsを追加",
        },
        {
            "ts": "2026-05-08T09:00:00+00:00",
            "skill_name": "aws-cdk-deploy",
            "strategy": "error_guided",
            "score_before": 0.5,
            "score_after": 0.65,
            "patch_summary": "description を短縮",
        },
    ]


# ── テストクラス ──────────────────────────────────────────

class TestEvalType1PatternExtraction:
    """型1: trigger_eval_generator の出力 eval_set から precision/recall を計算。"""

    def test_returns_eval_result(self, runner, eval_set_with_labels):
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert isinstance(result, EvalResult)

    def test_pipeline_type_is_pattern_extraction(self, runner, eval_set_with_labels):
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert result.pipeline_type == "pattern_extraction"

    def test_skill_name_preserved(self, runner, eval_set_with_labels):
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert result.skill_name == "aws-cdk-deploy"

    def test_eval_count_matches_input(self, runner, eval_set_with_labels):
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert result.eval_count == len(eval_set_with_labels)

    def test_convergence_cycles_is_zero(self, runner, eval_set_with_labels):
        """型1は convergence_cycles = 0 固定。"""
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert result.convergence_cycles == 0

    def test_precision_in_range(self, runner, eval_set_with_labels):
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert 0.0 <= result.trigger_precision <= 1.0

    def test_recall_in_range(self, runner, eval_set_with_labels):
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert 0.0 <= result.trigger_recall <= 1.0

    def test_precision_recall_with_all_should_trigger_true(self, runner):
        """全クエリが should_trigger=True のケース: recall=1.0, precision は TP/(TP+FP)。

        eval_set の全エントリが should_trigger=True のとき、
        FN=0 なので recall=1.0。
        FP=0 なので precision=1.0。
        """
        eval_set = [
            {"query": "CDK deploy", "should_trigger": True},
            {"query": "CDK synth", "should_trigger": True},
            {"query": "CDK stack", "should_trigger": True},
        ]
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set)
        assert result.trigger_recall == 1.0
        assert result.trigger_precision == 1.0

    def test_precision_recall_with_all_should_trigger_false(self, runner):
        """全クエリが should_trigger=False のケース: recall=0.0, precision=0.0。"""
        eval_set = [
            {"query": "チャンネル動画", "should_trigger": False},
            {"query": "全く関係ない", "should_trigger": False},
        ]
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set)
        assert result.trigger_recall == 0.0
        assert result.trigger_precision == 0.0

    def test_empty_eval_set(self, runner):
        """空の eval_set でも EvalResult が返る（precision/recall=0.0）。"""
        result = runner.run_pattern_extraction("aws-cdk-deploy", [])
        assert result.eval_count == 0
        assert result.trigger_precision == 0.0
        assert result.trigger_recall == 0.0

    def test_details_contains_tp_fp_fn(self, runner, eval_set_with_labels):
        """details に TP/FP/FN が含まれること。"""
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_with_labels)
        assert "tp" in result.details
        assert "fp" in result.details
        assert "fn" in result.details


class TestEvalType2PromptOptimization:
    """型2: evolution_memory.load_patterns から収束サイクルを推定。"""

    def test_returns_eval_result(self, runner, eval_set_with_labels, patterns_two_entries):
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert isinstance(result, EvalResult)

    def test_pipeline_type_is_prompt_optimization(self, runner, eval_set_with_labels, patterns_two_entries):
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert result.pipeline_type == "prompt_optimization"

    def test_skill_name_preserved(self, runner, eval_set_with_labels, patterns_two_entries):
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert result.skill_name == "aws-cdk-deploy"

    def test_eval_count_matches_input(self, runner, eval_set_with_labels, patterns_two_entries):
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert result.eval_count == len(eval_set_with_labels)

    def test_convergence_cycles_from_patterns(self, runner, eval_set_with_labels, patterns_two_entries):
        """convergence_cycles はパターン件数から導出される（>0 であること）。"""
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert result.convergence_cycles == len(patterns_two_entries)

    def test_convergence_cycles_zero_when_no_patterns(self, runner, eval_set_with_labels):
        """パターンが0件のとき convergence_cycles=0。"""
        with patch("pipeline_eval.load_patterns", return_value=[]):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert result.convergence_cycles == 0

    def test_precision_recall_in_range(self, runner, eval_set_with_labels, patterns_two_entries):
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert 0.0 <= result.trigger_precision <= 1.0
        assert 0.0 <= result.trigger_recall <= 1.0

    def test_load_patterns_called_with_skill_name_and_limit(self, runner, eval_set_with_labels, patterns_two_entries):
        """load_patterns が正しい skill_name と limit=1000 で呼ばれること。"""
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries) as mock_lp:
            runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        mock_lp.assert_called_once_with("aws-cdk-deploy", limit=1000)

    def test_details_contains_pattern_count(self, runner, eval_set_with_labels, patterns_two_entries):
        """details に pattern_count が含まれること。"""
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_with_labels)
        assert "pattern_count" in result.details
        assert result.details["pattern_count"] == len(patterns_two_entries)


class TestCompareReport:
    """compare() が2つの EvalResult から ComparisonReport を生成する。"""

    @pytest.fixture
    def result_type1(self):
        return EvalResult(
            pipeline_type="pattern_extraction",
            skill_name="aws-cdk-deploy",
            trigger_precision=0.8,
            trigger_recall=0.75,
            convergence_cycles=0,
            eval_count=10,
            details={"tp": 6, "fp": 2, "fn": 2},
        )

    @pytest.fixture
    def result_type2(self):
        return EvalResult(
            pipeline_type="prompt_optimization",
            skill_name="aws-cdk-deploy",
            trigger_precision=0.9,
            trigger_recall=0.85,
            convergence_cycles=3,
            eval_count=10,
            details={"pattern_count": 3},
        )

    def test_returns_comparison_report(self, runner, result_type1, result_type2):
        report = runner.compare([result_type1, result_type2])
        assert isinstance(report, ComparisonReport)

    def test_skill_name_in_report(self, runner, result_type1, result_type2):
        report = runner.compare([result_type1, result_type2])
        assert report.skill_name == "aws-cdk-deploy"

    def test_results_list_in_report(self, runner, result_type1, result_type2):
        report = runner.compare([result_type1, result_type2])
        assert len(report.results) == 2

    def test_winner_is_higher_precision_recall(self, runner, result_type1, result_type2):
        """precision + recall の合計が高い方が winner になる。"""
        report = runner.compare([result_type1, result_type2])
        # type2: 0.9+0.85=1.75 > type1: 0.8+0.75=1.55
        assert report.winner == "prompt_optimization"

    def test_winner_type1_when_higher(self, runner):
        """型1が高い場合は pattern_extraction が winner。"""
        r1 = EvalResult(
            pipeline_type="pattern_extraction",
            skill_name="my-skill",
            trigger_precision=0.95,
            trigger_recall=0.90,
            convergence_cycles=0,
            eval_count=5,
            details={},
        )
        r2 = EvalResult(
            pipeline_type="prompt_optimization",
            skill_name="my-skill",
            trigger_precision=0.7,
            trigger_recall=0.6,
            convergence_cycles=2,
            eval_count=5,
            details={},
        )
        report = runner.compare([r1, r2])
        assert report.winner == "pattern_extraction"

    def test_summary_is_non_empty_string(self, runner, result_type1, result_type2):
        report = runner.compare([result_type1, result_type2])
        assert isinstance(report.summary, str)
        assert len(report.summary) > 0

    def test_summary_contains_winner(self, runner, result_type1, result_type2):
        """summary に winner の pipeline_type が含まれること。"""
        report = runner.compare([result_type1, result_type2])
        assert report.winner in report.summary

    def test_single_result_compare(self, runner, result_type1):
        """1件のみでも compare が動作すること。"""
        report = runner.compare([result_type1])
        assert report.winner == "pattern_extraction"
        assert isinstance(report.summary, str)

    def test_empty_results_compare(self, runner):
        """空リストでも ComparisonReport が返ること。"""
        report = runner.compare([])
        assert isinstance(report, ComparisonReport)
        assert report.winner == ""
        assert report.skill_name == ""

    def test_mixed_skill_name_raises(self, runner):
        """異スキル名混在時に ValueError が発生すること。"""
        r1 = EvalResult(
            pipeline_type="pattern_extraction",
            skill_name="skill-a",
            trigger_precision=0.8,
            trigger_recall=0.8,
            convergence_cycles=0,
            eval_count=5,
            details={},
        )
        r2 = EvalResult(
            pipeline_type="prompt_optimization",
            skill_name="skill-b",
            trigger_precision=0.9,
            trigger_recall=0.9,
            convergence_cycles=2,
            eval_count=5,
            details={},
        )
        with pytest.raises(ValueError, match="skill_name"):
            runner.compare([r1, r2])


class TestPredictedTrigger:
    """predicted_trigger フィールドによる FP/FN 実測値テスト。"""

    def test_type1_fp_fn_with_predicted_trigger(self, runner):
        """predicted_trigger による FP/FN が実測値から算出されること。"""
        eval_set = [
            {"query": "CDK deploy", "should_trigger": True, "predicted_trigger": True},   # TP
            {"query": "CDK stack", "should_trigger": True, "predicted_trigger": False},   # FN
            {"query": "動画ダウンロード", "should_trigger": False, "predicted_trigger": True},  # FP
            {"query": "全く関係ない", "should_trigger": False, "predicted_trigger": False},  # TN
        ]
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set)
        # TP=1, FP=1, FN=1
        assert result.details["tp"] == 1
        assert result.details["fp"] == 1
        assert result.details["fn"] == 1
        # precision = 1/(1+1) = 0.5
        assert result.trigger_precision == pytest.approx(0.5)
        # recall = 1/(1+1) = 0.5
        assert result.trigger_recall == pytest.approx(0.5)

    def test_type1_backward_compat_no_predicted_trigger(self, runner):
        """predicted_trigger がない場合は should_trigger と同じとみなす（後方互換）。"""
        eval_set = [
            {"query": "CDK deploy", "should_trigger": True},
            {"query": "CDK synth", "should_trigger": True},
            {"query": "動画ダウンロード", "should_trigger": False},
        ]
        result = runner.run_pattern_extraction("aws-cdk-deploy", eval_set)
        # FP=0, FN=0 → precision=recall=1.0（should_trigger=Trueが2件）
        assert result.details["fp"] == 0
        assert result.details["fn"] == 0
        assert result.trigger_precision == 1.0
        assert result.trigger_recall == 1.0

    def test_type1_and_type2_differ_with_different_predicted_trigger(self, runner):
        """型1と型2で異なる predicted_trigger を持たせると比較結果が異なること。

        型1の eval_set: FNあり（一部見逃し）→ recall < 1.0
        型2の eval_set: FNなし（完全検出）→ recall = 1.0
        これにより compare() の winner が型2になることを確認する。
        """
        # 型1用: FNあり（predicted_trigger=False の should_trigger=True エントリが1件）
        eval_set_type1 = [
            {"query": "CDK deploy", "should_trigger": True, "predicted_trigger": True},   # TP
            {"query": "CDK synth", "should_trigger": True, "predicted_trigger": False},   # FN
            {"query": "動画ダウンロード", "should_trigger": False, "predicted_trigger": False},  # TN
        ]
        # 型2用: 完全検出（すべて predicted_trigger=True）
        eval_set_type2 = [
            {"query": "CDK deploy", "should_trigger": True, "predicted_trigger": True},   # TP
            {"query": "CDK synth", "should_trigger": True, "predicted_trigger": True},    # TP
            {"query": "動画ダウンロード", "should_trigger": False, "predicted_trigger": False},  # TN
        ]

        result_type1 = runner.run_pattern_extraction("aws-cdk-deploy", eval_set_type1)
        with patch("pipeline_eval.load_patterns", return_value=[]):
            result_type2 = runner.run_prompt_optimization("aws-cdk-deploy", eval_set_type2)

        # 型1: TP=1, FN=1 → recall = 0.5
        assert result_type1.trigger_recall == pytest.approx(0.5)
        # 型2: TP=2, FN=0 → recall = 1.0
        assert result_type2.trigger_recall == pytest.approx(1.0)

        # 結果が異なること（比較が有意であることの確認）
        assert result_type1.trigger_recall != result_type2.trigger_recall

        # compare() で型2が winner になること
        report = runner.compare([result_type1, result_type2])
        assert report.winner == "prompt_optimization"

    def test_type2_fp_fn_with_predicted_trigger(self, runner, patterns_two_entries):
        """型2でも predicted_trigger による FP/FN が実測値から算出されること。"""
        eval_set = [
            {"query": "CDK deploy", "should_trigger": True, "predicted_trigger": True},   # TP
            {"query": "CDK stack", "should_trigger": True, "predicted_trigger": False},   # FN
            {"query": "動画ダウンロード", "should_trigger": False, "predicted_trigger": True},  # FP
        ]
        with patch("pipeline_eval.load_patterns", return_value=patterns_two_entries):
            result = runner.run_prompt_optimization("aws-cdk-deploy", eval_set)
        # precision = 1/(1+1) = 0.5, recall = 1/(1+1) = 0.5
        assert result.trigger_precision == pytest.approx(0.5)
        assert result.trigger_recall == pytest.approx(0.5)
