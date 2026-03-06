"""Tests for strategy_router module."""
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts")
)
from strategy_router import STRATEGY_THRESHOLD, select_strategy


class TestSelectStrategy:
    def test_threshold_constant(self):
        assert STRATEGY_THRESHOLD == 200

    @pytest.mark.parametrize(
        "file_lines, expected",
        [
            (0, "self_refine"),
            (1, "self_refine"),
            (199, "self_refine"),
            (200, "budget_mpo"),
            (201, "budget_mpo"),
            (500, "budget_mpo"),
        ],
    )
    def test_strategy_selection(self, file_lines, expected):
        assert select_strategy(file_lines) == expected

    def test_negative_raises_value_error(self):
        with pytest.raises(ValueError, match="non-negative"):
            select_strategy(-1)
