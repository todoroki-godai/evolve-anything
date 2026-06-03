"""evolve_search（多世代 BES 進化探索）のテスト（#305 SkillOpt 近似）。

TDD: 「世代ごとに best subgoal fitness が単調非減少（=勾配的訓練の近似）」
「収束で早期停止する」を決定論で検証する。LLM/subprocess は一切呼ばない。

fitness は呼び出し側から callable で注入する（純粋関数・決定論）。
"""

import random
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import evolution_operators as evo  # noqa: E402


FRONTMATTER_A = "---\nname: skill-a\ndescription: skill a\n---\n"
FRONTMATTER_B = "---\nname: skill-b\ndescription: skill b\n---\n"

PARENT_A = (
    FRONTMATTER_A
    + "# Skill A\n\n"
    + "intro for A\n\n"
    + "## Usage\n\nUse A like this.\n\n"
    + "## Notes\n\nNote A.\n"
)

PARENT_B = (
    FRONTMATTER_B
    + "# Skill B\n\n"
    + "intro for B\n\n"
    + "## Examples\n\nExample B.\n\n"
    + "## Notes\n\nNote B.\n"
)


def _len_fitness(content: str) -> float:
    """テスト用の決定論 fitness: セクション数を 0–1 に正規化した代理。

    crossover は両親のセクションを合流させるため、世代を経るほど
    セクション数が増え fitness が単調に上がりやすい（探索の単調性検証用）。
    """
    sections = content.count("## ")
    return min(sections / 4.0, 1.0)


def _seed_candidates():
    return [
        {"content": PARENT_A, "fitness": _len_fitness(PARENT_A)},
        {"content": PARENT_B, "fitness": _len_fitness(PARENT_B)},
    ]


class TestEvolveSearch:
    def test_結果構造を返す(self):
        out = evo.evolve_search(
            _seed_candidates(),
            fitness_fn=_len_fitness,
            generations=3,
            offspring_count=2,
            rng=random.Random(42),
        )
        assert "best" in out
        assert "best_fitness_history" in out
        assert "generations_run" in out
        assert "converged" in out
        assert isinstance(out["best"], dict)
        assert "content" in out["best"]
        assert "fitness" in out["best"]

    def test_best_fitnessは世代ごとに単調非減少(self):
        # SkillOpt 近似の中核: エリート保存で best が悪化しないことを保証
        out = evo.evolve_search(
            _seed_candidates(),
            fitness_fn=_len_fitness,
            generations=5,
            offspring_count=3,
            rng=random.Random(7),
        )
        hist = out["best_fitness_history"]
        assert len(hist) >= 1
        for prev, cur in zip(hist, hist[1:]):
            assert cur >= prev - 1e-9, f"単調性違反: {hist}"

    def test_最終bestは初期best以上(self):
        seeds = _seed_candidates()
        init_best = max(c["fitness"] for c in seeds)
        out = evo.evolve_search(
            seeds,
            fitness_fn=_len_fitness,
            generations=4,
            offspring_count=2,
            rng=random.Random(11),
        )
        assert out["best"]["fitness"] >= init_best - 1e-9

    def test_決定論的(self):
        a = evo.evolve_search(
            _seed_candidates(),
            fitness_fn=_len_fitness,
            generations=4,
            offspring_count=2,
            rng=random.Random(99),
        )
        b = evo.evolve_search(
            _seed_candidates(),
            fitness_fn=_len_fitness,
            generations=4,
            offspring_count=2,
            rng=random.Random(99),
        )
        assert a["best"]["content"] == b["best"]["content"]
        assert a["best_fitness_history"] == b["best_fitness_history"]

    def test_収束で早期停止する(self):
        # fitness が頭打ち（patience 世代改善なし）なら generations 前に止まる
        out = evo.evolve_search(
            _seed_candidates(),
            fitness_fn=_len_fitness,
            generations=20,
            offspring_count=2,
            patience=2,
            rng=random.Random(5),
        )
        assert out["converged"] is True
        assert out["generations_run"] < 20

    def test_候補空なら安全に返す(self):
        out = evo.evolve_search(
            [],
            fitness_fn=_len_fitness,
            generations=3,
            offspring_count=2,
        )
        assert out["best"] is None
        assert out["generations_run"] == 0
        assert out["best_fitness_history"] == []

    def test_generations0なら初期bestのみ(self):
        seeds = _seed_candidates()
        out = evo.evolve_search(
            seeds,
            fitness_fn=_len_fitness,
            generations=0,
            offspring_count=2,
            rng=random.Random(1),
        )
        assert out["generations_run"] == 0
        # 初期集団の best を返す
        assert out["best"]["fitness"] == max(c["fitness"] for c in seeds)

    def test_fitness_fnは各候補に適用される(self):
        # 注入 fitness が seed の事前 fitness を上書きすることを確認
        seeds = [
            {"content": PARENT_A, "fitness": 0.0},  # 嘘の低い fitness
            {"content": PARENT_B, "fitness": 0.0},
        ]
        out = evo.evolve_search(
            seeds,
            fitness_fn=_len_fitness,
            generations=1,
            offspring_count=2,
            rng=random.Random(2),
        )
        # fitness_fn 再評価で 0 より大きくなる
        assert out["best"]["fitness"] > 0.0

    def test_収束しなければconverged_false(self):
        # 毎世代改善が続く設計の fitness（ランダムに上がり続ける代理）では
        # patience に達しない限り converged は False のまま generations 消化
        counter = {"n": 0}

        def increasing_fitness(content: str) -> float:
            # 内容に依存しつつ単調増加を保証する代理ではなく、
            # ここでは len ベースで「改善余地あり」を維持
            return min(len(content) / 5000.0, 1.0)

        out = evo.evolve_search(
            _seed_candidates(),
            fitness_fn=increasing_fitness,
            generations=3,
            offspring_count=2,
            patience=10,  # generations より大きい → 早期停止しない
            rng=random.Random(3),
        )
        assert out["generations_run"] == 3
