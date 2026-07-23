#!/usr/bin/env python3
"""evolve-loop-orchestrator のテスト"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# run_loop.py を importlib で読み込む（bin/evolve-loop との一貫性）
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
                "candidates": [
                    {
                        "id": "test_variant_0",
                        "content": "# 改善版スキル\n改善された内容。\n",
                    },
                    {
                        "id": "test_variant_1",
                        "content": "# 別の改善版\n別の改善された内容。\n",
                    },
                ],
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

    def test_keystone_generate_variants_not_mocked_e2e(self, tmp_path):
        """keystone regression test: generate_variants を一切 mock せず、
        run_loop() を dry-run で end-to-end 実行する。

        #234 の背景: 旧 generate_variants() は genetic-prompt-optimizer/scripts/
        optimize.py を `--generations 1 --population <N>` で subprocess 呼び出し
        していたが、この2オプションは optimize.py 側で廃止済み
        (`_DEPRECATED_OPTIONS`) のため、dry-run 含め常時失敗していた。既存
        テストは全て generate_variants 自体を patch.object で mock していたため
        このバグは検出されずに埋もれていた。実配線
        （run_loop → variant_generation.generate_variants → optimize_core の
        低レベル関数）を通しで検証できるのは本テストのみであり、これが今回の
        バグを実際に検出できる唯一の形のテストである。
        """
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text(
            "---\ndescription: テストスキル\n---\n\n# テスト\nテスト内容です。\n",
            encoding="utf-8",
        )
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # generate_variants は一切 mock しない（旧実装のバグは patch.object 越しでは
        # 検出できなかったため）。dry-run のため claude CLI（LLM）は呼ばれない前提。
        # 注意: collect_context 経由で git（pj_slug 解決）等の非LLM subprocess が
        # 呼ばれることがあるため、subprocess.run の呼び出し回数自体は検証しない
        # （0回である保証は本テストの目的ではない。variant_generation.py 単体の
        # 「dry_run で subprocess.run が0回」検証は test_variant_generation.py が担う）。
        results = run_loop_mod.run_loop(
            target_path=str(skill_file),
            loops=1,
            population=2,
            dry_run=True,
            output_dir=str(output_dir),
        )

        assert len(results) == 1
        assert results[0]["dry_run"] is True
        assert results[0]["variants_count"] == 2
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
                "candidates": [
                    {"id": "v0", "content": "# V0\n内容"},
                ],
            }

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=3,
                population=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            assert len(results) == 3

    def test_history_file_created(self, tmp_path, monkeypatch):
        """履歴が store の per-slug ファイルに記録される（ADR-031: 旧 output_dir/history.jsonl から集約）"""
        import optimize_history_store as store
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "testproj")

        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("# テスト\nテスト", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            mock_gen.return_value = {
                "candidates": [
                    {"id": "v0", "content": "# V0\n内容"},
                ],
            }

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )

            history_file = store.history_path("testproj")
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
                "candidates": [
                    {"id": "v0", "content": "# V0\n内容"},
                ],
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

    def test_verdict_in_history_jsonl(self, tmp_path, monkeypatch):
        """store の per-slug 履歴に verdict が記録される（ADR-031）"""
        import optimize_history_store as store
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "testproj")

        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:
            mock_gen.return_value = {
                "candidates": [
                    {"id": "v0", "content": "# V0\n内容"},
                ],
            }
            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                dry_run=True,
                output_dir=str(output_dir),
            )
            history_file = store.history_path("testproj")
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
                "candidates": [
                    {"id": "v0", "content": "# V0\n内容"},
                ],
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
                "candidates": [
                    {"id": "v0", "content": improved_content},
                ],
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
            "candidates": [
                {"id": "v0", "content": self.SKILL + "\n## Examples\n\nex0\n"},
                {"id": "v1", "content": self.SKILL + "\n## Tips\n\ntip1\n"},
            ],
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
            # 多世代探索 (#305) で進化 best が 1 件追加される（元 2 + evolved_best 1）
            assert results[0]["variants_count"] >= 3
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

    def test_進化bestを1件_variantと同形で返す(self):
        # #305: 多世代探索で勝ち残った best を 1 件だけ返す（LLM 採点を 1 候補に限定）
        variants = [
            {"id": "v0", "score": 0.7, "axes": {}, "content": "---\nx: 1\n---\n# A\n\n## U\n\nu\n", "content_length": 10},
            {"id": "v1", "score": 0.6, "axes": {}, "content": "---\nx: 1\n---\n# B\n\n## V\n\nv\n", "content_length": 10},
        ]
        out = run_loop_mod._evolve_variants(
            variants, "/dummy", global_best_content=None, dry_run=True
        )
        assert len(out) == 1
        ev = out[0]
        assert set(["id", "score", "axes", "content", "content_length"]).issubset(ev)
        # 多世代探索のテレメトリが付随する
        assert "evolve_search" in ev
        assert "generations_run" in ev["evolve_search"]

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
                "candidates": [
                    {"id": "axis_killer", "content": "# Bad\n内容"},
                ],
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
                "candidates": [
                    {"id": "regressed_v0", "content": regressed_content},
                ],
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
                "candidates": [
                    {"id": "stable_v0", "content": "# Same\n似た内容"},
                ],
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
                "candidates": [
                    {"id": "v0", "content": "# V0\n内容"},
                ],
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


class TestSelectionReeval:
    """採用前再評価（selection re-eval, winner's curse 補正 #234 PR2）のテスト。

    population=1 の最小構成。_score_variant_axes の side_effect で Step 3 の
    単発評価（1回目呼び出し）と selection_reeval の追加 n 回評価（2回目以降）
    を呼び出し順で制御する。
    """

    BASELINE = {
        "integrated_score": 0.60,
        "scores": {
            "technical": {"total": 0.60},
            "domain_quality": {"total": 0.60},
            "structure": {"total": 0.60},
        },
    }

    def _skill_file(self, tmp_path):
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("---\ndescription: test\n---\n# Test\n内容\n", encoding="utf-8")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        return skill_file, output_dir

    @staticmethod
    def _uniform_axes(v):
        return {"technical": v, "domain": v, "structure": v, "integrated": v}

    def _sequenced_score_fn(self, sequence):
        """呼び出し順に sequence の値を軸別均一スコアとして返す side_effect。"""
        calls = {"n": 0}

        def _fn(content, target_path, dry_run=False):
            idx = calls["n"]
            calls["n"] += 1
            return self._uniform_axes(sequence[idx])

        return _fn, calls

    def test_downgrade_improved_to_stable(self, tmp_path):
        """再評価で IMPROVED→STABLE に格下げされる。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        # 1回目=Step3単発(0.85, IMPROVED相当) / 以降3回=再評価(平均0.60=STABLE)
        score_fn, calls = self._sequenced_score_fn([0.85, 0.62, 0.60, 0.58])

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        assert calls["n"] == 4
        r = results[0]
        assert r["verdict"] == "STABLE"
        assert r["approved"] is False
        assert r["global_best_score"] == pytest.approx(0.60)
        assert r["selection_reeval_ran"] is True
        assert r["selection_reeval_downgraded"] is True
        assert r["selection_reeval_mean_score"] == pytest.approx(0.60)
        assert r["pre_reeval_score"] == pytest.approx(0.85)
        # 対象ファイルは変更されていない
        assert "# V0" not in skill_file.read_text(encoding="utf-8")

    def test_improvement_maintained_uses_corrected_mean(self, tmp_path):
        """再評価後も改善維持。global_best_score は単発値でなく補正後の平均値になる（核心的回帰テスト）。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        # 1回目=Step3単発(0.85) / 以降3回=再評価(平均0.80, 単発とは異なる値)
        score_fn, calls = self._sequenced_score_fn([0.85, 0.80, 0.82, 0.78])

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        r = results[0]
        assert r["verdict"] == "IMPROVED"
        assert r["approved"] is True
        # 単発値(0.85)でなく再評価平均(0.80)が採用されている
        assert r["global_best_score"] == pytest.approx(0.80)
        assert r["best_score"] == pytest.approx(0.80)
        assert r["pre_reeval_score"] == pytest.approx(0.85)
        assert r["selection_reeval_downgraded"] is False

    def test_downgrade_does_not_reach_input(self, tmp_path, monkeypatch):
        """格下げ後、人間確認 input() には到達しない。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        score_fn, calls = self._sequenced_score_fn([0.85, 0.62, 0.60, 0.58])

        def _fail_input(*args, **kwargs):
            raise AssertionError("input() should not be called after downgrade")

        monkeypatch.setattr("builtins.input", _fail_input)

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=False,
                dry_run=False,
                output_dir=str(output_dir),
            )

        assert results[0]["verdict"] == "STABLE"

    def test_downgrade_pitfall_not_double_recorded(self, tmp_path):
        """再評価で REGRESSED に格下げされても pitfall 記録は1回のみ（二重記録なし）。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        # 1回目=Step3単発(0.90) / 以降3回=再評価(平均0.40 → REGRESSED)
        score_fn, calls = self._sequenced_score_fn([0.90, 0.40, 0.42, 0.38])

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE), \
             patch.object(run_loop_mod, "_record_pitfall") as mock_pitfall:
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        assert results[0]["verdict"] == "REGRESSED"
        regression_calls = [
            c for c in mock_pitfall.call_args_list
            if len(c.args) >= 2 and c.args[1] == "regression"
        ]
        assert len(regression_calls) == 1

    def test_pareto_stable_skips_reeval(self, tmp_path):
        """Pareto 非優越で既に STABLE に格下げされた場合、再評価は発火しない（コスト最適化）。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        baseline = {
            "integrated_score": 0.72,
            "scores": {
                "technical": {"total": 0.70},
                "domain_quality": {"total": 0.70},
                "structure": {"total": 0.80},
            },
        }
        axes = {"technical": 0.40, "domain": 0.95, "structure": 0.80, "integrated": 0.78}

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_axes, \
             patch.object(run_loop_mod, "get_baseline_score", return_value=baseline):
            mock_gen.return_value = {"candidates": [{"id": "axis_killer", "content": "# Bad\n内容"}]}
            mock_axes.return_value = axes

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        assert results[0]["verdict"] == "STABLE"
        assert results[0]["selection_reeval_ran"] is False
        # Step3 の population 分（1回）のみ。再評価の追加呼び出しなし
        assert mock_axes.call_count == 1

    def test_initial_stable_skips_reeval(self, tmp_path):
        """verdict が最初から STABLE（Pareto チェック前）なら再評価は発火しない。"""
        skill_file, output_dir = self._skill_file(tmp_path)

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_axes, \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}
            # improvement = 0.61 - 0.60 = 0.01 <= epsilon(0.05) → STABLE
            mock_axes.return_value = self._uniform_axes(0.61)

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        assert results[0]["verdict"] == "STABLE"
        assert results[0]["selection_reeval_ran"] is False
        assert mock_axes.call_count == 1

    def test_disabled_skips_reeval(self, tmp_path):
        """selection_reeval_enabled=False（--no-selection-reeval 相当）で無効化される。"""
        skill_file, output_dir = self._skill_file(tmp_path)

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes") as mock_axes, \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}
            mock_axes.return_value = self._uniform_axes(0.85)

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
                selection_reeval_enabled=False,
            )

        assert results[0]["verdict"] == "IMPROVED"
        assert results[0]["selection_reeval_enabled"] is False
        assert results[0]["selection_reeval_ran"] is False
        assert results[0]["global_best_score"] == pytest.approx(0.85)
        # population 分のみ（1回）。再評価は発火しない
        assert mock_axes.call_count == 1

    def test_enabled_by_default(self, tmp_path):
        """引数省略時、再評価はデフォルトで有効。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        score_fn, calls = self._sequenced_score_fn([0.85, 0.80, 0.82, 0.78])

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        assert results[0]["selection_reeval_enabled"] is True
        assert results[0]["selection_reeval_ran"] is True
        assert calls["n"] == 4

    def test_configurable_n(self, tmp_path):
        """selection_reeval_n が設定可能（既定3以外の値でも動く）。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        score_fn, calls = self._sequenced_score_fn([0.85, 0.80, 0.82, 0.78, 0.81, 0.79])

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
                selection_reeval_n=5,
            )

        assert results[0]["selection_reeval_n"] == 5
        assert len(results[0]["selection_reeval_raw_scores"]) == 5
        assert calls["n"] == 6

    def test_dry_run_uses_dummy_score_no_subprocess(self, tmp_path):
        """dry_run 時は subprocess を呼ばずダミースコアで動作する。

        既知の限界: dry-run のダミースコアは content の MD5 ハッシュのみに依存
        する決定論関数なので、N回再評価しても常に同一値が返り分散ゼロ・
        格下げは発生し得ない（バグではなく既存の性質の自然な延長）。
        """
        skill_file, output_dir = self._skill_file(tmp_path)
        # dry-run ダミースコア base=0.77 の content（事前計算済み、technical/domain/
        # structure/integrated 全軸で baseline(0.65) を Pareto 優越する）
        candidate_content = "# reeval dry run candidate\n"

        import optimize_history_store as store

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(store, "resolve_slug", return_value="testproj"), \
             patch.object(run_loop_mod.subprocess, "run") as mock_subprocess:
            # subprocess.run は単一モジュールを全 caller が共有するため、
            # 履歴 slug 解決（pj_slug 経由の git 呼び出し）は先に無効化して
            # LLM 経路（claude -p）以外の subprocess 呼び出しを排除する。
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": candidate_content}]}
            mock_subprocess.side_effect = AssertionError("subprocess.run should not be called in dry_run")

            results = run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=True,
                output_dir=str(output_dir),
            )

        mock_subprocess.assert_not_called()
        r = results[0]
        assert r["verdict"] == "IMPROVED"
        assert r["selection_reeval_ran"] is True
        # 決定論ダミースコアは分散ゼロ → 格下げは起きない、平均は単発値と同一
        # （dry_run なので Step5 の H_best 適用自体はスキップされる。既存の
        # 「dry_run 時はファイル/状態を変更しない」設計であり本テストの対象外）
        assert r["selection_reeval_downgraded"] is False
        assert r["selection_reeval_std"] == pytest.approx(0.0)
        assert r["best_score"] == pytest.approx(r["pre_reeval_score"])

    def test_summary_output_contains_reeval_tag(self, tmp_path, capsys):
        """発火した場合、サマリー出力に再評価タグ文言が含まれる。"""
        skill_file, output_dir = self._skill_file(tmp_path)
        score_fn, calls = self._sequenced_score_fn([0.85, 0.80, 0.82, 0.78])

        with patch.object(run_loop_mod, "generate_variants") as mock_gen, \
             patch.object(run_loop_mod, "_score_variant_axes", side_effect=score_fn), \
             patch.object(run_loop_mod, "get_baseline_score", return_value=self.BASELINE):
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                auto=True,
                dry_run=False,
                output_dir=str(output_dir),
            )

        captured = capsys.readouterr()
        assert "reeval:" in captured.out
        assert "選定後再評価: 有効" in captured.out

    def test_header_shows_disabled_when_no_selection_reeval(self, tmp_path, capsys):
        """--no-selection-reeval 相当時、ヘッダーに「無効」が表示される。"""
        skill_file, output_dir = self._skill_file(tmp_path)

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:
            mock_gen.return_value = {"candidates": [{"id": "v0", "content": "# V0\n内容"}]}

            run_loop_mod.run_loop(
                target_path=str(skill_file),
                loops=1,
                population=1,
                dry_run=True,
                output_dir=str(output_dir),
                selection_reeval_enabled=False,
            )

        captured = capsys.readouterr()
        assert "選定後再評価: 無効" in captured.out


class TestEdgeCases:
    """エッジケースのテスト"""

    def test_empty_variants(self, tmp_path):
        """バリエーションが空の場合"""
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("# テスト\nテスト", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch.object(run_loop_mod, "generate_variants") as mock_gen:

            mock_gen.return_value = {"candidates": []}

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
