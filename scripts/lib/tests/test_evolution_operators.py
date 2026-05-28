"""evolution_operators モジュールのテスト（BES 前向き進化探索）。

TDD: 決定論的進化演算子を検証する。LLM/subprocess は一切呼ばない。
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


# ── crossover ─────────────────────────────────────────────────────────


class TestCrossover:
    def test_両親のセクションを含む(self):
        child = evo.crossover(PARENT_A, PARENT_B)
        # parent_a 固有セクション
        assert "## Usage" in child
        # parent_b 固有セクション
        assert "## Examples" in child

    def test_frontmatterはparent_aから取る(self):
        child = evo.crossover(PARENT_A, PARENT_B)
        assert child.startswith("---")
        assert "name: skill-a" in child.split("---")[1]
        # parent_b の frontmatter は含まない
        assert "name: skill-b" not in child

    def test_決定論的(self):
        c1 = evo.crossover(PARENT_A, PARENT_B)
        c2 = evo.crossover(PARENT_A, PARENT_B)
        assert c1 == c2

    def test_frontmatterなし両親でも例外なし(self):
        child = evo.crossover("# A\n\n## X\n\nx\n", "# B\n\n## Y\n\ny\n")
        assert "## X" in child
        assert "## Y" in child


# ── mutate ────────────────────────────────────────────────────────────


class TestMutate:
    def test_決定論的(self):
        m1 = evo.mutate(PARENT_A)
        m2 = evo.mutate(PARENT_A)
        assert m1 == m2

    def test_重複行を除去する(self):
        content = "## S\n\nline\nline\nother\n"
        out = evo.mutate(content)
        # 連続重複は 1 つに畳まれる
        assert out.count("line\n") <= 1 or out.count("\nline\n") <= 1

    def test_corrections付きでも例外なし(self):
        out = evo.mutate(PARENT_A, corrections=["apply foobar fix"])
        assert isinstance(out, str)
        assert len(out) > 0

    def test_corrections付きも決定論的(self):
        corr = ["apply foobar fix"]
        assert evo.mutate(PARENT_A, corrections=corr) == evo.mutate(
            PARENT_A, corrections=corr
        )


# ── select_parents ────────────────────────────────────────────────────


class TestSelectParents:
    def _candidates(self):
        return [
            {"content": "a", "fitness": 0.9},
            {"content": "b", "fitness": 0.5},
            {"content": "c", "fitness": 0.1},
        ]

    def test_k個返す(self):
        rng = random.Random(42)
        out = evo.select_parents(self._candidates(), k=4, rng=rng)
        assert len(out) == 4

    def test_rng固定で再現可能(self):
        out1 = evo.select_parents(self._candidates(), k=5, rng=random.Random(42))
        out2 = evo.select_parents(self._candidates(), k=5, rng=random.Random(42))
        assert [c["content"] for c in out1] == [c["content"] for c in out2]

    def test_全fitness0でも例外なくk個(self):
        cands = [
            {"content": "a", "fitness": 0.0},
            {"content": "b", "fitness": 0.0},
        ]
        out = evo.select_parents(cands, k=3, rng=random.Random(1))
        assert len(out) == 3

    def test_負fitnessでも例外なし(self):
        cands = [
            {"content": "a", "fitness": -1.0},
            {"content": "b", "fitness": -0.5},
        ]
        out = evo.select_parents(cands, k=2, rng=random.Random(1))
        assert len(out) == 2

    def test_候補空ならk個欲しくても空を返す(self):
        out = evo.select_parents([], k=3)
        assert out == []

    def test_rng未指定でも動作する(self):
        out = evo.select_parents(self._candidates(), k=2)
        assert len(out) == 2


# ── evolve_generation ─────────────────────────────────────────────────


class TestEvolveGeneration:
    def _candidates(self):
        return [
            {"content": PARENT_A, "fitness": 0.8},
            {"content": PARENT_B, "fitness": 0.6},
        ]

    def test_offspring_count個返す(self):
        out = evo.evolve_generation(
            self._candidates(), offspring_count=3, rng=random.Random(42)
        )
        assert len(out) == 3
        for child in out:
            assert "content" in child
            assert isinstance(child["content"], str)

    def test_決定論的(self):
        out1 = evo.evolve_generation(
            self._candidates(), offspring_count=2, rng=random.Random(7)
        )
        out2 = evo.evolve_generation(
            self._candidates(), offspring_count=2, rng=random.Random(7)
        )
        assert [c["content"] for c in out1] == [c["content"] for c in out2]

    def test_候補1個でも動作する(self):
        out = evo.evolve_generation(
            [{"content": PARENT_A, "fitness": 0.5}],
            offspring_count=2,
            rng=random.Random(1),
        )
        assert len(out) == 2

    def test_候補空なら空を返す(self):
        out = evo.evolve_generation([], offspring_count=3)
        assert out == []

    def test_corrections付きでも動作する(self):
        out = evo.evolve_generation(
            self._candidates(),
            offspring_count=2,
            corrections=["apply foobar fix"],
            rng=random.Random(3),
        )
        assert len(out) == 2
