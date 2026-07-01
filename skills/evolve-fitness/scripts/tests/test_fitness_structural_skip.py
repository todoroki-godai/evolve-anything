"""#105: fitness_evolution.is_structural_skip の単一ソース判定テスト。

Step 2 の fitness 生成提案（evolve）と calibration_drift（audit）が同じ predicate で
「この PJ では fitness を使わない設計（skill 提案が構造的に出ない）」を判定できるよう
共有ヘルパを検証する。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve-fitness" / "scripts"))

import fitness_evolution as fe


class TestIsStructuralSkip:
    def test_insufficient_data_with_structural_reason_is_skip(self):
        result = {
            "status": "insufficient_data",
            "structural_reason": "skill_evolve_not_scored",
        }
        assert fe.is_structural_skip(result) is True

    def test_insufficient_data_without_structural_reason_not_skip(self):
        """structural_reason 欠落の insufficient_data は構造的スキップ扱いにしない。"""
        result = {"status": "insufficient_data"}
        assert fe.is_structural_skip(result) is False

    def test_bootstrap_is_skip(self):
        """bootstrap は structural_reason を持たない契約だが構造シグナルとして畳む（#584）。"""
        result = {"status": "bootstrap", "data_count": 6}
        assert fe.is_structural_skip(result) is True

    def test_ready_is_not_skip(self):
        result = {"status": "ready"}
        assert fe.is_structural_skip(result) is False

    def test_none_and_empty_not_skip(self):
        assert fe.is_structural_skip(None) is False
        assert fe.is_structural_skip({}) is False

    def test_real_zero_data_run_is_structural_skip(self):
        """実 run（データ 0 件）は insufficient_data + structural_reason → 構造的スキップ。"""
        result = fe.run_fitness_evolution(history=[])
        assert result["status"] == "insufficient_data"
        assert fe.is_structural_skip(result) is True
