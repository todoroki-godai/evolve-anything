"""#105: annotate_fitness_generation_advice のテスト。

has_fitness=false の PJ で fitness_evolution が構造的スキップ（skill_evolve_not_scored /
bootstrap）と判定していれば generation_advised=false を付与し、Step 2 の生成提案（AskUserQuestion）
と fitness_evolution の「fitness は使わない設計」が矛盾しないようにする。
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "rl"))
sys.path.insert(0, str(_PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"))

import evolve  # noqa: E402


def _fitness_phase(has_fitness=False):
    return {"has_fitness": has_fitness, "fitness_functions": []}


class TestAnnotateFitnessGenerationAdvice:
    def test_structural_skip_sets_generation_advised_false(self):
        """skill_evolve_not_scored → generation_advised=false + note。"""
        fit = _fitness_phase(has_fitness=False)
        fe_result = {
            "status": "insufficient_data",
            "structural_reason": "skill_evolve_not_scored",
        }
        evolve.annotate_fitness_generation_advice(fit, fe_result)
        assert fit["generation_advised"] is False
        assert "generation_note" in fit
        assert "スキップ" in fit["generation_note"]

    def test_bootstrap_sets_generation_advised_false(self):
        fit = _fitness_phase(has_fitness=False)
        evolve.annotate_fitness_generation_advice(fit, {"status": "bootstrap"})
        assert fit["generation_advised"] is False

    def test_ready_sets_generation_advised_true(self):
        """構造的スキップでない（ready）→ generation_advised=true（従来どおり生成提案）。"""
        fit = _fitness_phase(has_fitness=False)
        evolve.annotate_fitness_generation_advice(fit, {"status": "ready"})
        assert fit["generation_advised"] is True
        assert "generation_note" not in fit

    def test_has_fitness_true_untouched(self):
        """既に fitness がある PJ は back-annotate しない。"""
        fit = _fitness_phase(has_fitness=True)
        evolve.annotate_fitness_generation_advice(
            fit, {"status": "insufficient_data", "structural_reason": "skill_evolve_not_scored"}
        )
        assert "generation_advised" not in fit

    def test_non_dict_fitness_phase_is_safe(self):
        """fitness phase 不在（None）でも例外を出さない。"""
        assert evolve.annotate_fitness_generation_advice(None, {"status": "bootstrap"}) is None
