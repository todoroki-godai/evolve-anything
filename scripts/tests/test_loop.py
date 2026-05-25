"""run-loop.py の攻撃者エージェント関連関数のテスト。"""
import sys
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_LIB_DIR = _SCRIPTS_DIR / "lib"
_LOOP_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "skills"
    / "rl-loop-orchestrator"
    / "scripts"
)
for _p in [str(_SCRIPTS_DIR), str(_LIB_DIR), str(_LOOP_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

# run-loop.py の関数をインポート
# モジュールとしてインポートするためにファイル名から "-" を含むため importlib を使う
import importlib.util

_LOOP_PY = _LOOP_SCRIPTS_DIR / "run_loop.py"
_spec = importlib.util.spec_from_file_location("run_loop_mod", _LOOP_PY)
_mod = importlib.util.module_from_spec(_spec)
# scorer_prompts / line_limit / skill_evolve のインポートを事前にモック
sys.modules.setdefault("scorer_prompts", mock.MagicMock(
    DEFAULT_AXIS_WEIGHTS={"technical": 0.4, "domain": 0.4, "structure": 0.2},
    get_axis_prompts=lambda: {
        "technical": "tech {content}",
        "domain": "domain {content}",
        "structure": "struct {content}",
    },
))
sys.modules.setdefault("line_limit", mock.MagicMock(check_line_limit=lambda *a, **kw: True))
sys.modules.setdefault("skill_evolve", mock.MagicMock(
    assess_single_skill=lambda *a, **kw: {},
    evolve_skill_proposal=lambda *a, **kw: {},
    apply_evolve_proposal=lambda *a, **kw: {"applied": False, "error": "mock"},
))
_spec.loader.exec_module(_mod)

from score_noise import compute_stats, to_confidence_interval
from scorer_schema import ConfidenceInterval

compute_disagreement_score = _mod.compute_disagreement_score
run_adversarial_agent = _mod.run_adversarial_agent
run_loop_with_adversarial = _mod.run_loop_with_adversarial


# ──────────────────────────────────────────────────────
# test_compute_disagreement_score_low
# ──────────────────────────────────────────────────────

def test_compute_disagreement_score_low():
    """スコアが揃っているとき disagreement が低い（< 0.1）。"""
    scores = [0.7, 0.71, 0.69]
    result = compute_disagreement_score(scores)
    assert result < 0.1


# ──────────────────────────────────────────────────────
# test_compute_disagreement_score_high
# ──────────────────────────────────────────────────────

def test_compute_disagreement_score_high():
    """スコアがバラけているとき disagreement が高い（> 0.1）。"""
    scores = [0.3, 0.6, 0.9]
    result = compute_disagreement_score(scores)
    assert result > 0.1


# ──────────────────────────────────────────────────────
# test_run_adversarial_agent_failure_returns_empty
# ──────────────────────────────────────────────────────

def test_run_adversarial_agent_failure_returns_empty():
    """`subprocess.run` が失敗したとき `""` を返す。"""
    with mock.patch("subprocess.run", side_effect=Exception("subprocess failed")):
        result = run_adversarial_agent("some skill content", [0.7, 0.8, 0.75])
    assert result == ""


# ──────────────────────────────────────────────────────
# test_run_loop_with_adversarial_high_disagreement_warns
# ──────────────────────────────────────────────────────

def test_run_loop_with_adversarial_high_disagreement_warns(capsys, tmp_path):
    """disagreement > 0.15 のとき警告が出力される。"""
    # score_variant をモック: 高いばらつきを再現するため交互に異なる値を返す
    call_count = {"n": 0}

    def mock_score_variant(content, target_path, dry_run=False):
        scores = [0.3, 0.7, 0.9]
        val = scores[call_count["n"] % len(scores)]
        call_count["n"] += 1
        return val

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Test Skill\nsome content")

    with mock.patch.object(_mod, "score_variant", side_effect=mock_score_variant), \
         mock.patch.object(_mod, "run_adversarial_agent", return_value=""):
        result = run_loop_with_adversarial(
            str(skill_file),
            n_evaluators=3,
            adversarial=False,
        )

    captured = capsys.readouterr()
    assert "評価者間で意見が割れています" in captured.out


# ──────────────────────────────────────────────────────
# test_run_loop_with_adversarial_ci_structure
# ──────────────────────────────────────────────────────

def test_run_loop_with_adversarial_ci_structure(tmp_path):
    """返り値に `ci` フィールドがあり `ConfidenceInterval` 型。"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Test Skill\nsome content")

    with mock.patch.object(_mod, "score_variant", return_value=0.7), \
         mock.patch.object(_mod, "run_adversarial_agent", return_value=""):
        result = run_loop_with_adversarial(
            str(skill_file),
            n_evaluators=3,
            adversarial=False,
        )

    assert "ci" in result
    assert isinstance(result["ci"], ConfidenceInterval)
