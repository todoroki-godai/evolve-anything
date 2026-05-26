"""tests for skill_evolve.rubric.rubric_checkpoint (issue #231)."""
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_lib_dir))

import pytest
from skill_evolve.rubric import rubric_checkpoint, DIFF_BUDGET


# ---------------------------------------------------------------------------
# propose フェーズ
# ---------------------------------------------------------------------------

def test_rubric_checkpoint_propose_all_pass():
    """全フィールド入り proposal_dict で全チェック passed。"""
    proposal = {
        "pitfalls_template": "some pitfall content",
        "sections_to_add": "## Pre-flight\ncheck\n## Failure-triggered Learning\nlearn",
        "diff_lines": 10,
    }
    result = rubric_checkpoint("propose", proposal)

    assert result["phase"] == "propose"
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["pitfalls"]["passed"] is True
    assert checks["pre_flight"]["passed"] is True
    assert checks["trigger"]["passed"] is True
    assert checks["diff_budget"]["passed"] is True
    assert checks["diff_budget"]["actual"] == 10

    # stdout_lines に phase 名と全チェック名が含まれることを確認
    joined = "\n".join(result["stdout_lines"])
    assert "propose" in joined
    assert "pitfalls" in joined
    assert "pre_flight" in joined
    assert "trigger" in joined
    assert "diff_budget" in joined
    assert "✔" in joined


def test_rubric_checkpoint_propose_trigger_missing():
    """sections_to_add に Failure-triggered がない場合、trigger チェックが False になる。"""
    proposal = {
        "pitfalls_template": "some pitfall content",
        "sections_to_add": "## Pre-flight\ncheck only",  # Failure-triggered なし
        "diff_lines": 5,
    }
    result = rubric_checkpoint("propose", proposal)

    checks = {c["name"]: c for c in result["checks"]}
    assert checks["trigger"]["passed"] is False

    # stdout に ✘ が含まれる
    joined = "\n".join(result["stdout_lines"])
    assert "✘" in joined
    assert "missing" in joined


def test_rubric_checkpoint_diff_over_budget():
    """diff_lines が DIFF_BUDGET+1 で diff_budget チェックが False になる。"""
    over_budget = DIFF_BUDGET + 1  # 31 lines
    proposal = {
        "pitfalls_template": "pitfall",
        "sections_to_add": "## Pre-flight\n## Failure-triggered Learning",
        "diff_lines": over_budget,
    }
    result = rubric_checkpoint("propose", proposal)

    checks = {c["name"]: c for c in result["checks"]}
    assert checks["diff_budget"]["passed"] is False
    assert checks["diff_budget"]["actual"] == over_budget

    joined = "\n".join(result["stdout_lines"])
    assert f"{over_budget}/{DIFF_BUDGET}" in joined


def test_rubric_checkpoint_diff_exactly_at_budget():
    """diff_lines が DIFF_BUDGET ちょうどで diff_budget チェックが True になる。"""
    proposal = {
        "pitfalls_template": "pitfall",
        "sections_to_add": "## Pre-flight\n## Failure-triggered Learning",
        "diff_lines": DIFF_BUDGET,
    }
    result = rubric_checkpoint("propose", proposal)

    checks = {c["name"]: c for c in result["checks"]}
    assert checks["diff_budget"]["passed"] is True
    assert checks["diff_budget"]["actual"] == DIFF_BUDGET


# ---------------------------------------------------------------------------
# apply フェーズ
# ---------------------------------------------------------------------------

def test_rubric_checkpoint_apply():
    """apply フェーズで correction_ids キーの存在チェックが動く。"""
    # correction_ids あり → passed
    result_pass = rubric_checkpoint("apply", {"correction_ids": ["#42"]})
    checks_pass = {c["name"]: c for c in result_pass["checks"]}
    assert result_pass["phase"] == "apply"
    assert checks_pass["reason_refs"]["passed"] is True

    joined = "\n".join(result_pass["stdout_lines"])
    assert "apply" in joined
    assert "reason_refs" in joined
    assert "✔" in joined

    # correction_ids なし → failed
    result_fail = rubric_checkpoint("apply", {})
    checks_fail = {c["name"]: c for c in result_fail["checks"]}
    assert checks_fail["reason_refs"]["passed"] is False

    joined_fail = "\n".join(result_fail["stdout_lines"])
    assert "✘" in joined_fail


def test_rubric_checkpoint_apply_empty_correction_ids():
    """空リストの correction_ids は False になる。"""
    result = rubric_checkpoint("apply", {"correction_ids": []})
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["reason_refs"]["passed"] is False


# ---------------------------------------------------------------------------
# stdout_lines 構造
# ---------------------------------------------------------------------------

def test_rubric_stdout_lines_structure():
    """stdout_lines の先頭行に [rubric] が含まれる。"""
    result = rubric_checkpoint("propose", {"diff_lines": 0})
    assert result["stdout_lines"][0].startswith("├── [rubric] propose:")
    for line in result["stdout_lines"][1:]:
        assert line.startswith("│")
