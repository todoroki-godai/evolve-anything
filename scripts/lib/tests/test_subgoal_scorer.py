"""subgoal_scorer モジュールのテスト。

TDD: 正常系E2Eテスト → 異常系テスト の順で記述する。
LLM 呼び出しなし（決定論で完結）。
"""

import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from subgoal_scorer import SubgoalResult, SubgoalScorerResult, score_subgoals


# ── フィクスチャ ──────────────────────────────────────────────────────

FRONTMATTER = "---\nname: my-skill\ndescription: test skill\n---\n"

CANDIDATE_FULL = (
    FRONTMATTER
    + "# My Skill\n\n"
    + "Trigger: when user asks to do something\n\n"
    + "Use this skill to handle foobar corrections.\n"
    + "extracted_learning: apply foobar fix\n"
)

CORRECTIONS_BASIC = [
    {
        "message": "foobar was not handled correctly",
        "correction_type": "behavior",
        "extracted_learning": "apply foobar fix",
        "last_skill": "my-skill",
        "reflect_status": "pending",
    }
]


# ── 正常系E2Eテスト ───────────────────────────────────────────────────


class TestScoreSubgoalsE2E:
    """score_subgoals() の正常系E2Eテスト。"""

    def test_返り値の構造が正しい(self):
        """total と subgoals キーが存在し型が正しい。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# My Skill\n",
            corrections=CORRECTIONS_BASIC,
        )
        assert isinstance(result, SubgoalScorerResult)
        assert isinstance(result.total, float)
        assert isinstance(result.subgoals, list)
        assert 0.0 <= result.total <= 1.0

    def test_サブゴールの構造が正しい(self):
        """各 subgoal に goal/score/passed/detail キーが存在する。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# My Skill\n",
            corrections=CORRECTIONS_BASIC,
        )
        assert len(result.subgoals) >= 1
        for sg in result.subgoals:
            assert isinstance(sg, SubgoalResult)
            assert isinstance(sg.goal, str)
            assert isinstance(sg.score, float)
            assert isinstance(sg.passed, bool)
            assert isinstance(sg.detail, str)
            assert 0.0 <= sg.score <= 1.0

    def test_frontmatter保持サブゴール_正常(self):
        """frontmatter が保持されていれば frontmatter サブゴールが passed=True。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# Original\n",
            corrections=[],
        )
        fm_sg = next(sg for sg in result.subgoals if sg.goal == "frontmatter_preserved")
        assert fm_sg.passed is True
        assert fm_sg.score == 1.0

    def test_trigger網羅率サブゴール_正常(self):
        """候補に Trigger 行があれば trigger サブゴールが passed=True。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# My Skill\nTrigger: original trigger\n",
            corrections=[],
        )
        trigger_sg = next(sg for sg in result.subgoals if sg.goal == "trigger_coverage")
        assert trigger_sg.passed is True
        assert trigger_sg.score == 1.0

    def test_correction対応サブゴール_キーワード反映済み(self):
        """corrections の extracted_learning が候補に含まれれば passed=True。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# My Skill\n",
            corrections=CORRECTIONS_BASIC,
        )
        corr_sg = next(sg for sg in result.subgoals if sg.goal == "correction_addressed")
        assert corr_sg.passed is True
        assert corr_sg.score >= 0.5

    def test_line_budget_サブゴール_正常(self):
        """candidate が 500 行以内なら line_budget サブゴールが passed=True。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# My Skill\n",
            corrections=[],
            max_lines=500,
        )
        budget_sg = next(sg for sg in result.subgoals if sg.goal == "line_budget")
        assert budget_sg.passed is True
        assert budget_sg.score == 1.0

    def test_total_は_サブゴール加重平均(self):
        """total は各 subgoal の score の平均であること。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=FRONTMATTER + "# My Skill\n",
            corrections=CORRECTIONS_BASIC,
        )
        scores = [sg.score for sg in result.subgoals]
        expected_avg = sum(scores) / len(scores)
        assert abs(result.total - expected_avg) < 1e-9

    def test_corrections_なしでも動作する(self):
        """corrections が空リストでも total は NaN にならず 0.0–1.0 の範囲内。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=None,
            corrections=[],
        )
        assert not (result.total != result.total)  # NaN チェック
        assert 0.0 <= result.total <= 1.0

    def test_original_なしでも動作する(self):
        """original が None でも frontmatter サブゴールは passed=True（比較不要）。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=None,
            corrections=[],
        )
        fm_sg = next(sg for sg in result.subgoals if sg.goal == "frontmatter_preserved")
        # original なしは「保持確認不要」→ pass
        assert fm_sg.passed is True


# ── 異常系テスト ──────────────────────────────────────────────────────


class TestScoreSubgoalsEdgeCases:
    """異常系・エッジケースのテスト。"""

    def test_空candidateでもNaNにならない(self):
        """empty candidate でも total が NaN / 例外にならない。"""
        result = score_subgoals(
            candidate="",
            original=None,
            corrections=[],
        )
        assert not (result.total != result.total)  # NaN チェック
        assert 0.0 <= result.total <= 1.0

    def test_サブゴール0件ならtotalは0_0(self):
        """subgoals が0件（あり得ないが）でも total=0.0 でフォールバック。"""
        # score_subgoals を直接テストするのではなく、内部の _aggregate をテスト
        from subgoal_scorer import _aggregate_subgoals

        assert _aggregate_subgoals([]) == 0.0

    def test_frontmatter消失サブゴール_失敗(self):
        """frontmatter が消えた場合 frontmatter サブゴールが passed=False。"""
        result = score_subgoals(
            candidate="# No Frontmatter\n",
            original=FRONTMATTER + "# Original\n",
            corrections=[],
        )
        fm_sg = next(sg for sg in result.subgoals if sg.goal == "frontmatter_preserved")
        assert fm_sg.passed is False
        assert fm_sg.score == 0.0

    def test_trigger消失サブゴール_失敗(self):
        """original に Trigger があり candidate にない場合 trigger サブゴールが passed=False。"""
        result = score_subgoals(
            candidate=FRONTMATTER + "# My Skill\n(no trigger here)\n",
            original=FRONTMATTER + "# My Skill\nTrigger: original trigger\n",
            corrections=[],
        )
        trigger_sg = next(sg for sg in result.subgoals if sg.goal == "trigger_coverage")
        assert trigger_sg.passed is False

    def test_correction未反映サブゴール_低スコア(self):
        """corrections の extracted_learning が candidate に未反映なら低スコア。"""
        result = score_subgoals(
            candidate=FRONTMATTER + "# My Skill\n(unrelated content)\n",
            original=FRONTMATTER + "# My Skill\n",
            corrections=[
                {
                    "message": "use xyz approach",
                    "correction_type": "behavior",
                    "extracted_learning": "xyz_unique_keyword_12345",
                    "last_skill": "my-skill",
                }
            ],
        )
        corr_sg = next(sg for sg in result.subgoals if sg.goal == "correction_addressed")
        assert corr_sg.score < 0.5

    def test_line_budget超過サブゴール_失敗(self):
        """candidate が max_lines 超過なら line_budget サブゴールが passed=False。"""
        over_limit = FRONTMATTER + "\n".join(f"line {i}" for i in range(600))
        result = score_subgoals(
            candidate=over_limit,
            original=FRONTMATTER + "# Original\n",
            corrections=[],
            max_lines=500,
        )
        budget_sg = next(sg for sg in result.subgoals if sg.goal == "line_budget")
        assert budget_sg.passed is False
        assert budget_sg.score == 0.0

    def test_slop_hookは存在するが常にpass(self):
        """slop_free サブゴールが存在し、デフォルトは passed=True（後日実装予定）。"""
        result = score_subgoals(
            candidate=CANDIDATE_FULL,
            original=None,
            corrections=[],
        )
        slop_sg = next(
            (sg for sg in result.subgoals if sg.goal == "slop_free"), None
        )
        assert slop_sg is not None
        assert slop_sg.passed is True  # 現時点ではフックのみ、常に pass
