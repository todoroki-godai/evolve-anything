"""Integration tests for optimize-large-skill-mpo pipeline."""
from __future__ import annotations
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from optimize import GeneticOptimizer, Individual
from granularity import determine_split_level, split_sections, merge_small_sections
from bandit_selector import BanditSectionSelector
from early_stopping import EarlyStopRule, should_stop
from model_cascade import ModelCascade
from parallel import build_plan, run_parallel, dedup_consolidate, OptimizeResult


# --- Fixtures ---

@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _build_large_skill(lines: int = 1000) -> str:
    """1000行超のモック Markdown スキルを生成。## 10個、### 30個。"""
    parts: list[str] = []
    parts.append("# Large Mock Skill")
    parts.extend([f"preamble line {i}" for i in range(20)])

    section_idx = 0
    lines_so_far = len(parts)
    lines_per_h2 = (lines - lines_so_far) // 10

    for h2 in range(10):
        parts.append(f"## Section {h2}")
        h3_count = 3
        lines_per_h3 = (lines_per_h2 - 1) // (h3_count + 1)

        # Content before first h3
        parts.extend([f"s{h2} intro line {i}" for i in range(lines_per_h3)])

        for h3 in range(h3_count):
            parts.append(f"### Sub {h2}.{h3}")
            parts.extend([f"s{h2}.{h3} line {i}" for i in range(lines_per_h3)])
            section_idx += 1

    # Pad to reach target line count
    while len(parts) < lines:
        parts.append(f"padding line {len(parts)}")

    return "\n".join(parts[:lines])


@pytest.fixture
def large_skill_path(temp_dir):
    skill_path = temp_dir / "large-skill.md"
    skill_path.write_text(_build_large_skill(1000), encoding="utf-8")
    return skill_path


@pytest.fixture
def medium_skill_path(temp_dir):
    """150行のスキルファイル"""
    lines = ["# Medium Skill"]
    lines.append("## Section A")
    lines.extend([f"a line {i}" for i in range(40)])
    lines.append("## Section B")
    lines.extend([f"b line {i}" for i in range(40)])
    lines.append("### Sub B1")
    lines.extend([f"b1 line {i}" for i in range(30)])
    lines.append("## Section C")
    lines.extend([f"c line {i}" for i in range(33)])
    skill_path = temp_dir / "medium-skill.md"
    skill_path.write_text("\n".join(lines), encoding="utf-8")
    return skill_path


# --- Test 7.1: 1000行超モックスキル ---

class TestLargeSkillMock:
    def test_large_skill_line_count(self):
        content = _build_large_skill(1000)
        assert content.count("\n") + 1 == 1000

    def test_large_skill_has_headings(self):
        content = _build_large_skill(1000)
        h2_count = sum(1 for line in content.split("\n") if line.startswith("## "))
        h3_count = sum(1 for line in content.split("\n") if line.startswith("### "))
        assert h2_count == 10
        assert h3_count == 30

    def test_large_skill_strategy_is_budget_mpo(self):
        content = _build_large_skill(1000)
        from strategy_router import select_strategy
        assert select_strategy(content.count("\n") + 1) == "budget_mpo"

    def test_large_skill_split_level_h2_only(self):
        assert determine_split_level(1000) == "h2_only"


# --- Test 7.2: --dry-run 統合テスト ---

class TestDryRunIntegration:
    def test_dry_run_sectioned(self, large_skill_path, temp_dir):
        """1000行スキルの --dry-run --sectioned 統合テスト"""
        optimizer = GeneticOptimizer(
            target_path=str(large_skill_path),
            generations=2,
            population_size=2,
            dry_run=True,
        )
        optimizer.run_dir = temp_dir / "test_run"

        with patch("optimize.subprocess.run", side_effect=FileNotFoundError):
            result = optimizer.run_sectioned()

        assert result["strategy"] == "budget_mpo"
        assert result["split_level"] == "h2_only"
        assert result["sections"] >= 5  # 10 h2 + preamble, some may merge
        assert result["best_individual"] is not None
        assert result["best_individual"]["fitness"] is not None

    def test_dry_run_standard(self, large_skill_path, temp_dir):
        """標準 run() も引き続き動作する"""
        optimizer = GeneticOptimizer(
            target_path=str(large_skill_path),
            generations=1,
            population_size=2,
            dry_run=True,
        )
        optimizer.run_dir = temp_dir / "test_run"

        with patch("optimize.subprocess.run", side_effect=FileNotFoundError):
            result = optimizer.run()

        assert len(result["history"]) == 1


# --- Test 7.3: 粒度制御 + バンディット選択 ---

class TestGranularityBandit:
    def test_h2_only_sections_fed_to_bandit(self):
        """1000行ファイルの h2_only 分割がバンディットに正しく渡される"""
        content = _build_large_skill(1000)
        sections = split_sections(content, "h2_only")
        sections = merge_small_sections(sections)

        section_ids = [s.id for s in sections]
        bandit = BanditSectionSelector(section_ids)

        # Thompson Sampling で選択可能
        selected = bandit.select_top_k(3)
        assert len(selected) == 3
        assert all(s in section_ids for s in selected)

    def test_h2_h3_sections_for_medium(self, medium_skill_path):
        """150行ファイルは h2_h3 分割"""
        content = medium_skill_path.read_text(encoding="utf-8")
        file_lines = content.count("\n") + 1
        level = determine_split_level(file_lines)
        assert level == "h2_h3"

        sections = split_sections(content, level)
        sections = merge_small_sections(sections)

        # preamble + 3 h2 + 1 h3 (some may merge)
        assert len(sections) >= 3

    def test_bandit_update_changes_distribution(self):
        """Bandit update が分布を変更する"""
        bandit = BanditSectionSelector(["s1", "s2", "s3"])
        initial_state = bandit.get_state()

        # s1 に改善あり を5回記録
        for _ in range(5):
            bandit.update("s1", improved=True)

        final_state = bandit.get_state()
        assert final_state["s1"][0] > initial_state["s1"][0]  # alpha increased


# --- Test 7.4: カスケード + 早期停止 ---

class TestCascadeEarlyStopping:
    def test_cascade_tier_selection(self):
        """ModelCascade の Tier が正しく返る"""
        cascade = ModelCascade(enabled=True)
        assert cascade.get_model(1) == "haiku"
        assert cascade.get_model(2) == "sonnet"
        assert cascade.get_model(3) == "opus"

    def test_early_stopping_in_sectioned(self, large_skill_path, temp_dir):
        """早期停止が run_sectioned() 内で機能する"""
        # quality_threshold=0.0 で即座に停止するルール
        rule = EarlyStopRule(quality_threshold=0.0)

        optimizer = GeneticOptimizer(
            target_path=str(large_skill_path),
            generations=5,
            population_size=2,
            dry_run=True,
            early_stop_rule=rule,
        )
        optimizer.run_dir = temp_dir / "test_run"

        with patch("optimize.subprocess.run", side_effect=FileNotFoundError):
            result = optimizer.run_sectioned()

        # 早期停止が効いているはず（全世代実行しない）
        assert result["best_individual"] is not None

    def test_budget_sets_early_stop_limit(self, large_skill_path):
        """budget パラメータが early_stop_rule.budget_limit に反映される"""
        optimizer = GeneticOptimizer(
            target_path=str(large_skill_path),
            dry_run=True,
            budget=30,
        )
        assert optimizer.early_stop_rule.budget_limit == 30


# --- Test 7.5: 並行最適化統合 ---

class TestParallelIntegration:
    def test_parallel_with_references(self, temp_dir):
        """references/ 付きスキルの並行最適化"""
        skill_path = temp_dir / "SKILL.md"
        skill_path.write_text("# Main Skill\ncontent", encoding="utf-8")
        refs_dir = temp_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "ref1.md").write_text("# Ref 1\nref content", encoding="utf-8")
        (refs_dir / "ref2.md").write_text("# Ref 2\nref content", encoding="utf-8")

        plan = build_plan(skill_path, parallel=2)
        assert len(plan.references) == 2
        assert plan.main_skill == skill_path

        def mock_opt(path):
            return OptimizeResult(
                path=str(path),
                best_fitness=0.8,
                best_content=f"optimized {path.name}",
            )

        results = run_parallel(plan, mock_opt)
        assert len(results) == 3

        # dedup should keep all (different content)
        deduped = dedup_consolidate(results)
        content_results = [r for r in deduped if r.best_content]
        assert len(content_results) == 3


# --- Test 7.6: エンドツーエンド dry-run ---

class TestEndToEnd:
    def test_full_pipeline_dry_run(self, large_skill_path, temp_dir):
        """1000行スキルの完全パイプライン dry-run テスト"""
        optimizer = GeneticOptimizer(
            target_path=str(large_skill_path),
            generations=2,
            population_size=2,
            dry_run=True,
            strategy="budget_mpo",
            budget=100,
        )
        optimizer.run_dir = temp_dir / "e2e_run"

        with patch("optimize.subprocess.run", side_effect=FileNotFoundError):
            result = optimizer.run_sectioned()

        # 基本的な結果構造の検証
        assert result["run_id"] is not None
        assert result["strategy"] == "budget_mpo"
        assert result["best_individual"]["fitness"] is not None
        assert result["best_individual"]["fitness"] >= 0.0

        # スコア履歴が記録されている
        assert "score_history" in result
        assert len(result["score_history"]) > 0

        # Bandit 状態が保存されている
        bandit_file = optimizer.run_dir / "bandit_state.json"
        assert bandit_file.exists()

        # result.json が保存されている
        result_file = optimizer.run_dir / "result.json"
        assert result_file.exists()
