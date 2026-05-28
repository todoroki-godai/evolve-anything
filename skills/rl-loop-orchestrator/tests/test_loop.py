#!/usr/bin/env python3
"""rl-loop-orchestrator のテスト"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# run_loop.py を importlib で読み込む（bin/rl-loop との一貫性）
import importlib.util
spec = importlib.util.spec_from_file_location(
    "run_loop",
    Path(__file__).parent.parent / "scripts" / "run_loop.py",
)
run_loop_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_loop_mod)


class TestGetBaselineScore:
    """ベースラインスコア取得のテスト"""

    def test_dry_run_returns_dummy(self):
        """dry-run 時はダミースコアを返す"""
        result = run_loop_mod.get_baseline_score("/dummy/path", dry_run=True)
        assert "integrated_score" in result
        assert isinstance(result["integrated_score"], float)
        assert 0.0 <= result["integrated_score"] <= 1.0

    def test_dry_run_has_scores_structure(self):
        """dry-run スコアに scores 構造がある"""
        result = run_loop_mod.get_baseline_score("/dummy/path", dry_run=True)
        assert "scores" in result
        assert "technical" in result["scores"]
        assert "domain_quality" in result["scores"]
        assert "structure" in result["scores"]


class TestScoreVariant:
    """バリエーションスコアリングのテスト"""

    def test_dry_run_returns_score(self):
        """dry-run 時はスコアを返す"""
        score = run_loop_mod.score_variant(
            "# テストスキル\nテスト内容", "/dummy/path", dry_run=True
        )
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_different_content_different_scores(self):
        """異なる内容は異なるスコアを返す（確率的）"""
        scores = set()
        for i in range(10):
            score = run_loop_mod.score_variant(
                f"# スキル バリエーション {i}\n内容 {i * 100}",
                "/dummy/path",
                dry_run=True,
            )
            scores.add(score)
        # 10個中少なくとも2種類以上のスコアがあるはず
        assert len(scores) >= 2


class TestRunLoop:
    """メインループのテスト"""

    def test_dry_run_completes(self, tmp_path):
        """dry-run で1ループが完走する"""
        # テスト用スキルファイルを作成
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text(
            "---\ndescription: テストスキル\n---\n\n# テスト\nテスト内容です。\n",
            encoding="utf-8",
        )

        # OUTPUT_DIR を一時ディレクトリに差し替え
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            # generate_variants のモック
            mock_gen.return_value = {
                "history": [{
                    "generation": 0,
                    "individuals": [
                        {
                            "id": "test_variant_0",
                            "content": "# 改善版スキル\n改善された内容。\n",
                        },
                        {
                            "id": "test_variant_1",
                            "content": "# 別の改善版\n別の改善された内容。\n",
                        },
                    ],
                }],
            }

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=2,
                dry_run=True,
                output_dir=str(output_dir),
            )

            assert len(results) == 1
            assert results[0]["dry_run"] is True
            assert "baseline_score" in results[0]
            assert "best_score" in results[0]

    def test_multiple_loops(self, tmp_path):
        """複数ループが実行される"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("# テスト\nテスト", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            mock_gen.return_value = {
                "history": [{
                    "generation": 0,
                    "individuals": [
                        {"id": "v0", "content": "# V0\n内容"},
                    ],
                }],
            }

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=3,
                population=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            assert len(results) == 3

    def test_history_file_created(self, tmp_path):
        """履歴ファイルが作成される"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("# テスト\nテスト", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            mock_gen.return_value = {
                "history": [{
                    "generation": 0,
                    "individuals": [
                        {"id": "v0", "content": "# V0\n内容"},
                    ],
                }],
            }

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            history_file = output_dir / "history.jsonl"
            assert history_file.exists()
            lines = history_file.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) >= 1
            record = json.loads(lines[0])
            assert "baseline_score" in record


class TestVerdict:
    """verdict（IMPROVED/STABLE/REGRESSED）のテスト"""

    def test_verdict_field_in_result(self, tmp_path):
        """結果に verdict フィールドが含まれる"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "v0", "content": "# V0\n内容"},
                ]}],
            }
            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )
            assert len(results) == 1
            assert "verdict" in results[0]
            assert results[0]["verdict"] in ("IMPROVED", "STABLE", "REGRESSED")

    def test_compute_verdict_improved(self):
        """epsilon より大きい改善は IMPROVED"""
        assert run_loop_mod._compute_verdict(0.10, epsilon=0.05) == "IMPROVED"

    def test_compute_verdict_stable(self):
        """epsilon 以内の変化は STABLE"""
        assert run_loop_mod._compute_verdict(0.03, epsilon=0.05) == "STABLE"
        assert run_loop_mod._compute_verdict(-0.03, epsilon=0.05) == "STABLE"
        assert run_loop_mod._compute_verdict(0.0, epsilon=0.05) == "STABLE"

    def test_compute_verdict_regressed(self):
        """epsilon より大きい悪化は REGRESSED"""
        assert run_loop_mod._compute_verdict(-0.10, epsilon=0.05) == "REGRESSED"

    def test_verdict_in_history_jsonl(self, tmp_path):
        """履歴ファイルに verdict が記録される"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "v0", "content": "# V0\n内容"},
                ]}],
            }
            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )
            history_file = output_dir / "history.jsonl"
            record = json.loads(history_file.read_text(encoding="utf-8").strip())
            assert "verdict" in record
            assert "global_best_score" in record


class TestHBest:
    """H_best 駆動（最良ハーネスから進化）のテスト"""

    def test_global_best_score_in_result(self, tmp_path):
        """結果に global_best_score フィールドが含まれる"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "v0", "content": "# V0\n内容"},
                ]}],
            }
            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )
            assert "global_best_score" in results[0]

    def test_hbest_used_as_baseline_in_subsequent_loops(self, tmp_path):
        """承認後、次ループの baseline は再採点せず H_best スコアを使う"""
        skill_file = tmp_path / "test-skill.md"
        original = "---\ndescription: test\n---\n# Original\n内容\n"
        skill_file.write_text(original, encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        improved_content = "---\ndescription: test\n---\n# Improved\n改善された内容\n"
        call_count = [0]

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_score, \
             patch.object(run_loop_mod, "get_baseline_score") as mock_baseline:

            mock_baseline.return_value = {"integrated_score": 0.60}
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "v0", "content": improved_content},
                ]}],
            }

            def score_side_effect(content, target_path, dry_run=False):
                call_count[0] += 1
                return {"technical": 0.85, "domain": 0.85, "structure": 0.85, "integrated": 0.85}

            mock_score.side_effect = score_side_effect

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=2,
                auto=True,  # 自動承認でループ間の H_best 更新を確認
                dry_run=False,
                output_dir=str(output_dir),
            )

            assert len(results) == 2
            # ループ1: IMPROVED（0.85 - 0.60 = 0.25 > epsilon）
            assert results[0]["verdict"] == "IMPROVED"
            # ループ2: baseline は再採点せず H_best スコア（0.85）を使う
            assert results[1]["baseline_score"] == pytest.approx(0.85)
            # get_baseline_score は1回だけ呼ばれる（初回のみ）
            assert mock_baseline.call_count == 1


class TestEvolveSearch:
    """BES 前向き進化探索 (--evolve-search, #256) のテスト。

    dry_run では _score_variant_axes / run_subgoal_scoring とも LLM 非依存
    （決定論）のため mock 不要。念のため LLM 経路の _score_single_axis を
    mock して、進化フェーズが LLM を呼ばないことを保証する。
    """

    SKILL = (
        "---\ndescription: test\n---\n# Test\n\nintro\n\n"
        "## Usage\n\nuse it\n\n## Notes\n\nnote\n"
    )

    def _mock_gen(self):
        return {
            "history": [{"generation": 0, "individuals": [
                {"id": "v0", "content": self.SKILL + "\n## Examples\n\nex0\n"},
                {"id": "v1", "content": self.SKILL + "\n## Tips\n\ntip1\n"},
            ]}],
        }

    def test_evolve_search_dry_run_completes(self, tmp_path):
        """--evolve-search 有りで dry-run が例外なく完走する。"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text(self.SKILL, encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_single_axis") as mock_axis:
            mock_gen.return_value = self._mock_gen()

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=2,
                dry_run=True,
                output_dir=str(output_dir),
                evolve_search=True,
            )

            assert len(results) == 1
            # 進化フェーズで variant が増えている（元 2 + 子 2）
            assert results[0]["variants_count"] >= 4
            # dry_run なので LLM 経路は呼ばれない
            mock_axis.assert_not_called()

    def test_evolve_search_best_not_below_single_pass(self, tmp_path):
        """進化版の best が 1パス版の best を下回らない経路が通る。

        _score_variant_axes を決定論 mock し、同一ベースラインで
        evolve_search なし版とあり版の best_score を比較する。
        """
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text(self.SKILL, encoding="utf-8")

        # content をキーに固定スコアを返す決定論 mock（LLM 非依存）
        def axes_for(content, target_path, dry_run=False):
            # 子（より長い結合テキスト）には高スコアを与える
            base = 0.60 + min(len(content), 2000) / 20000.0
            return {
                "technical": base, "domain": base,
                "structure": base, "integrated": round(base, 4),
            }

        def run_once(evolve_search, out):
            out.mkdir()
            with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
                 patch.object(run_loop_mod, "_score_variant_axes",
                              side_effect=axes_for):
                mock_gen.return_value = self._mock_gen()
                return run_loop_mod.run_loop(
                    target_path=str(skill_file),
                    loops=1,
                    population=2,
                    dry_run=True,
                    output_dir=str(out),
                    evolve_search=evolve_search,
                )

        single = run_once(False, tmp_path / "single")
        evolved = run_once(True, tmp_path / "evolved")

        assert evolved[0]["best_score"] >= single[0]["best_score"]


class TestEvolveVariantsHelper:
    """_evolve_variants ヘルパーの単体テスト（LLM 非依存）。"""

    def test_offspring_と同形のdictを返す(self):
        variants = [
            {"id": "v0", "score": 0.7, "axes": {}, "content": "---\nx: 1\n---\n# A\n\n## U\n\nu\n", "content_length": 10},
            {"id": "v1", "score": 0.6, "axes": {}, "content": "---\nx: 1\n---\n# B\n\n## V\n\nv\n", "content_length": 10},
        ]
        out = run_loop_mod._evolve_variants(
            variants, "/dummy", global_best_content=None, dry_run=True
        )
        assert len(out) == len(variants)
        for ev in out:
            assert set(["id", "score", "axes", "content", "content_length"]).issubset(ev)

    def test_空variantsなら空を返す(self):
        out = run_loop_mod._evolve_variants(
            [], "/dummy", global_best_content=None, dry_run=True
        )
        assert out == []


class TestParetoDominance:
    """Pareto dominance 判定のテスト"""

    def test_dominates_strict(self):
        """全軸で改善 → dominate"""
        challenger = {"technical": 0.80, "domain": 0.80, "structure": 0.80}
        defender = {"technical": 0.70, "domain": 0.70, "structure": 0.70}
        assert run_loop_mod._dominates(challenger, defender, tolerance=0.05) is True

    def test_does_not_dominate_axis_regression(self):
        """1軸だけ大きく劣化 → dominate しない"""
        challenger = {"technical": 0.50, "domain": 0.95, "structure": 0.80}
        defender = {"technical": 0.70, "domain": 0.70, "structure": 0.70}
        # technical が tolerance(0.05) を超えて劣化
        assert run_loop_mod._dominates(challenger, defender, tolerance=0.05) is False

    def test_dominates_within_tolerance(self):
        """tolerance 内の劣化は許容され、他軸が改善していれば dominate"""
        challenger = {"technical": 0.68, "domain": 0.85, "structure": 0.75}
        defender = {"technical": 0.70, "domain": 0.70, "structure": 0.70}
        # technical が -0.02（tolerance 内）、domain/structure は改善
        assert run_loop_mod._dominates(challenger, defender, tolerance=0.05) is True

    def test_does_not_dominate_equal(self):
        """全軸同等 → dominate しない（改善が1軸も無い）"""
        scores = {"technical": 0.70, "domain": 0.70, "structure": 0.70}
        assert run_loop_mod._dominates(scores, scores, tolerance=0.05) is False

    def test_no_axis_regression_blocks_improvement(self, tmp_path):
        """integrated 改善でも軸別劣化があれば IMPROVED にならない"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_axes, \
             patch.object(run_loop_mod, "get_baseline_score") as mock_baseline:

            # H_best: tech=0.70, dom=0.70, struct=0.80, integrated=0.72
            mock_baseline.return_value = {
                "integrated_score": 0.72,
                "scores": {
                    "technical": {"total": 0.70},
                    "domain_quality": {"total": 0.70},
                    "structure": {"total": 0.80},
                },
            }
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "axis_killer", "content": "# Bad\n内容"},
                ]}],
            }
            # variant: tech 0.40 (大幅劣化) なのに domain 0.95 で押し上げて integrated 0.78
            mock_axes.return_value = {
                "technical": 0.40,
                "domain": 0.95,
                "structure": 0.80,
                "integrated": 0.78,
            }

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

            # integrated は +0.06 改善だが、technical が -0.30 大幅劣化
            # → IMPROVED でなく PARTIAL（または STABLE 扱いでスキップ）
            assert results[0]["verdict"] != "IMPROVED"


class TestRegressedPitfalls:
    """REGRESSED verdict → pitfalls 自動転記のテスト"""

    def test_regressed_records_pitfall(self, tmp_path):
        """REGRESSED verdict 時に _record_pitfall が呼ばれる"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        regressed_content = "# Worse\n劣化した内容"

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_score, \
             patch.object(run_loop_mod, "get_baseline_score") as mock_baseline, \
             patch.object(run_loop_mod, "_record_pitfall") as mock_pitfall:

            mock_baseline.return_value = {"integrated_score": 0.80}
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "regressed_v0", "content": regressed_content},
                ]}],
            }
            mock_score.return_value = {"technical": 0.60, "domain": 0.60, "structure": 0.60, "integrated": 0.60}

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

            assert mock_pitfall.called
            call_args = mock_pitfall.call_args
            assert call_args.args[1] == "regression"
            assert "regressed_v0" in call_args.args[2]

    def test_stable_does_not_record_pitfall(self, tmp_path):
        """STABLE verdict 時は pitfall 記録しない"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_score, \
             patch.object(run_loop_mod, "get_baseline_score") as mock_baseline, \
             patch.object(run_loop_mod, "_record_pitfall") as mock_pitfall:

            mock_baseline.return_value = {"integrated_score": 0.80}
            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "stable_v0", "content": "# Same\n似た内容"},
                ]}],
            }
            mock_score.return_value = {"technical": 0.81, "domain": 0.81, "structure": 0.81, "integrated": 0.81}

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

            # source="regression" の呼び出しはない
            regression_calls = [
                c for c in mock_pitfall.call_args_list
                if len(c.args) >= 2 and c.args[1] == "regression"
            ]
            assert len(regression_calls) == 0

    def test_dry_run_does_not_record_pitfall(self, tmp_path):
        """dry_run 時は pitfall 記録しない（副作用なし）"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_record_pitfall") as mock_pitfall:

            mock_gen.return_value = {
                "history": [{"generation": 0, "individuals": [
                    {"id": "v0", "content": "# V0\n内容"},
                ]}],
            }

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            # dry_run では pitfall 記録は一切行わない
            regression_calls = [
                c for c in mock_pitfall.call_args_list
                if len(c.args) >= 2 and c.args[1] == "regression"
            ]
            assert len(regression_calls) == 0


class TestEdgeCases:
    """エッジケースのテスト"""

    def test_empty_variants(self, tmp_path):
        """バリエーションが空の場合"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("# テスト\nテスト", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            mock_gen.return_value = {"history": []}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            # バリエーションなしでもクラッシュしない
            assert len(results) == 0

    def test_generate_variants_error(self, tmp_path):
        """バリエーション生成エラー時"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("# テスト\nテスト", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            mock_gen.return_value = {"error": "テストエラー"}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            assert len(results) == 0
