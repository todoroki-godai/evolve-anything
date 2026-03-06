"""Tests for BanditSectionSelector and estimate_importance."""
from __future__ import annotations

import json
import random
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sys

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent / "scripts"
    ),
)

from bandit_selector import BanditSectionSelector, estimate_importance


SECTION_IDS = ["intro", "body", "conclusion", "examples", "refs"]


class TestInit:
    def test_all_sections_beta_1_1(self):
        sel = BanditSectionSelector(SECTION_IDS)
        for sid in SECTION_IDS:
            assert sel.alpha[sid] == 1.0
            assert sel.beta[sid] == 1.0

    def test_empty_sections(self):
        sel = BanditSectionSelector([])
        assert sel.alpha == {}
        assert sel.beta == {}


class TestSelectTopK:
    def test_returns_k_items(self):
        sel = BanditSectionSelector(SECTION_IDS)
        result = sel.select_top_k(3)
        assert len(result) == 3
        assert all(sid in SECTION_IDS for sid in result)

    def test_k_greater_than_n_returns_all(self):
        sel = BanditSectionSelector(SECTION_IDS)
        result = sel.select_top_k(10)
        assert set(result) == set(SECTION_IDS)

    def test_k_equal_n_returns_all(self):
        sel = BanditSectionSelector(SECTION_IDS)
        result = sel.select_top_k(len(SECTION_IDS))
        assert set(result) == set(SECTION_IDS)


class TestUpdate:
    def test_improved_increments_alpha(self):
        sel = BanditSectionSelector(SECTION_IDS)
        sel.update("intro", improved=True)
        assert sel.alpha["intro"] == 2.0
        assert sel.beta["intro"] == 1.0

    def test_not_improved_increments_beta(self):
        sel = BanditSectionSelector(SECTION_IDS)
        sel.update("intro", improved=False)
        assert sel.alpha["intro"] == 1.0
        assert sel.beta["intro"] == 2.0

    def test_unknown_section_logs_warning(self, caplog):
        sel = BanditSectionSelector(SECTION_IDS)
        sel.update("nonexistent", improved=True)
        assert "Unknown section_id" in caplog.text


class TestInitializeFromImportance:
    def test_positive_scores(self):
        sel = BanditSectionSelector(["a", "b", "c"])
        sel.initialize_from_importance({"a": 10.0, "b": 5.0, "c": 0.0})
        assert sel.alpha["a"] == 1.0 + 5.0  # normalized=1.0, scale=5.0
        assert sel.alpha["b"] == 1.0 + 2.5  # normalized=0.5
        assert sel.alpha["c"] == 1.0  # normalized=0.0

    def test_negative_scores_clamped(self):
        sel = BanditSectionSelector(["a", "b"])
        sel.initialize_from_importance({"a": 10.0, "b": -5.0})
        assert sel.alpha["a"] == 1.0 + 5.0
        assert sel.alpha["b"] == 1.0  # clamped to 0

    def test_all_negative_scores_noop(self):
        sel = BanditSectionSelector(["a", "b"])
        sel.initialize_from_importance({"a": -1.0, "b": -2.0})
        assert sel.alpha["a"] == 1.0
        assert sel.alpha["b"] == 1.0

    def test_empty_scores_noop(self):
        sel = BanditSectionSelector(["a"])
        sel.initialize_from_importance({})
        assert sel.alpha["a"] == 1.0

    def test_unknown_section_in_scores_ignored(self):
        sel = BanditSectionSelector(["a"])
        sel.initialize_from_importance({"a": 5.0, "unknown": 10.0})
        # "unknown" not in alpha, so it's skipped; "a" is normalized relative to max(5,10)=10
        assert sel.alpha["a"] == 1.0 + (5.0 / 10.0) * 5.0

    def test_custom_scale(self):
        sel = BanditSectionSelector(["a"])
        sel.initialize_from_importance({"a": 10.0}, scale=10.0)
        assert sel.alpha["a"] == 1.0 + 10.0


class TestGetState:
    def test_returns_correct_tuples(self):
        sel = BanditSectionSelector(["a", "b"])
        sel.update("a", improved=True)
        sel.update("b", improved=False)
        state = sel.get_state()
        assert state["a"] == (2.0, 1.0)
        assert state["b"] == (1.0, 2.0)


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        sel = BanditSectionSelector(["a", "b", "c"])
        sel.update("a", improved=True)
        sel.update("a", improved=True)
        sel.update("b", improved=False)
        sel.save_state(tmp_path)

        loaded = BanditSectionSelector.load_state(tmp_path, ["a", "b", "c"])
        assert loaded.alpha["a"] == 3.0
        assert loaded.beta["b"] == 2.0
        assert loaded.alpha["c"] == 1.0  # unchanged

    def test_save_creates_json_file(self, tmp_path):
        sel = BanditSectionSelector(["x"])
        sel.save_state(tmp_path)
        path = tmp_path / "bandit_state.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["x"] == [1.0, 1.0]

    def test_load_missing_file_returns_fresh(self, tmp_path):
        loaded = BanditSectionSelector.load_state(tmp_path, ["a", "b"])
        assert loaded.alpha["a"] == 1.0
        assert loaded.beta["a"] == 1.0

    def test_load_corrupted_file_returns_fresh(self, tmp_path):
        (tmp_path / "bandit_state.json").write_text("not json")
        loaded = BanditSectionSelector.load_state(tmp_path, ["a"])
        assert loaded.alpha["a"] == 1.0

    def test_load_partial_state(self, tmp_path):
        """State file has only some sections; missing ones get Beta(1,1)."""
        data = {"a": [5.0, 3.0]}
        (tmp_path / "bandit_state.json").write_text(json.dumps(data))
        loaded = BanditSectionSelector.load_state(tmp_path, ["a", "b"])
        assert loaded.alpha["a"] == 5.0
        assert loaded.beta["a"] == 3.0
        assert loaded.alpha["b"] == 1.0


class TestEstimateImportance:
    def test_n_plus_1_calls(self):
        sections = [
            {"id": "a", "content": "AAA"},
            {"id": "b", "content": "BBB"},
            {"id": "c", "content": "CCC"},
        ]
        full_content = "AAA\nBBB\nCCC"
        evaluator = MagicMock(return_value=0.8)

        result = estimate_importance(sections, evaluator, full_content)

        assert evaluator.call_count == 4  # 1 baseline + 3 ablations
        assert set(result.keys()) == {"a", "b", "c"}

    def test_importance_values(self):
        sections = [
            {"id": "a", "content": "important"},
            {"id": "b", "content": "filler"},
        ]
        full_content = "important\nfiller"

        def evaluator(content):
            if "important" in content and "filler" in content:
                return 1.0  # baseline
            elif "important" in content:
                return 0.9  # without filler
            else:
                return 0.3  # without important

        result = estimate_importance(sections, evaluator, full_content)
        assert result["a"] == pytest.approx(0.7)  # 1.0 - 0.3
        assert result["b"] == pytest.approx(0.1)  # 1.0 - 0.9

    def test_baseline_failure_returns_empty(self):
        evaluator = MagicMock(side_effect=RuntimeError("fail"))
        result = estimate_importance(
            [{"id": "a", "content": "x"}], evaluator, "x"
        )
        assert result == {}

    def test_ablation_failure_assigns_zero(self):
        call_count = 0

        def evaluator(content):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 0.8  # baseline
            raise RuntimeError("ablation fail")

        sections = [{"id": "a", "content": "x"}]
        result = estimate_importance(sections, evaluator, "x")
        assert result["a"] == 0.0


class TestThompsonSamplingBias:
    def test_high_alpha_selected_more_often(self):
        """alpha が大きいセクションが統計的に多く選択される。"""
        random.seed(42)
        sel = BanditSectionSelector(["strong", "weak"])
        sel.alpha["strong"] = 20.0  # strong prior
        sel.alpha["weak"] = 1.0

        counts = {"strong": 0, "weak": 0}
        trials = 1000
        for _ in range(trials):
            chosen = sel.select_top_k(1)
            counts[chosen[0]] += 1

        # strong should be selected much more often
        assert counts["strong"] > counts["weak"]
        assert counts["strong"] > trials * 0.8
