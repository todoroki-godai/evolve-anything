"""detect_repeated_correction_patterns のユニットテスト (#41)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from discover import detect_repeated_correction_patterns

CORR = lambda msg: {"message": msg, "correction_type": "user_correction"}


class TestDetectRepeatedCorrectionPatterns:
    def test_returns_empty_when_no_corrections(self):
        assert detect_repeated_correction_patterns([]) == []

    def test_returns_empty_below_threshold(self):
        corrections = [CORR("削除前に移動してください")] * 2
        result = detect_repeated_correction_patterns(corrections, threshold=3)
        assert result == []

    def test_detects_pattern_at_threshold(self):
        corrections = [CORR("削除前に移動してください")] * 3
        result = detect_repeated_correction_patterns(corrections, threshold=3)
        assert len(result) == 1
        assert result[0]["type"] == "hook_candidate"
        assert result[0]["count"] == 3
        assert "削除前" in result[0]["pattern"]

    def test_detects_multiple_patterns(self):
        corrections = [CORR("パターンA")] * 4 + [CORR("パターンB")] * 3
        result = detect_repeated_correction_patterns(corrections, threshold=3)
        assert len(result) == 2

    def test_skips_empty_messages(self):
        corrections = [{"message": ""}, {"message": None}, CORR("本物のミス")] * 3
        result = detect_repeated_correction_patterns(corrections, threshold=3)
        patterns = [r["pattern"] for r in result]
        assert all("本物のミス" in p for p in patterns)

    def test_includes_suggestion_field(self):
        corrections = [CORR("同じミス")] * 3
        result = detect_repeated_correction_patterns(corrections, threshold=3)
        assert result[0]["suggestion"] == "hook_candidate"

    def test_sorted_by_count_descending(self):
        corrections = [CORR("多いミス")] * 5 + [CORR("少ないミス")] * 3
        result = detect_repeated_correction_patterns(corrections, threshold=3)
        assert result[0]["count"] >= result[1]["count"]
