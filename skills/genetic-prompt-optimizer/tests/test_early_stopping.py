"""Tests for early_stopping module."""
import math
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from early_stopping import (
    MARGINAL_GAIN_THRESHOLD,
    PLATEAU_COUNT,
    QUALITY_THRESHOLD,
    EarlyStopRule,
    should_stop,
)


class TestEarlyStopRule:
    def test_defaults(self):
        rule = EarlyStopRule()
        assert rule.quality_threshold == QUALITY_THRESHOLD
        assert rule.plateau_count == PLATEAU_COUNT
        assert rule.budget_limit is None
        assert rule.marginal_gain_threshold == MARGINAL_GAIN_THRESHOLD

    def test_invalid_quality_threshold_negative(self):
        rule = EarlyStopRule(quality_threshold=-0.5)
        assert rule.quality_threshold == QUALITY_THRESHOLD

    def test_invalid_quality_threshold_over_one(self):
        rule = EarlyStopRule(quality_threshold=1.5)
        assert rule.quality_threshold == QUALITY_THRESHOLD

    def test_invalid_plateau_count(self):
        rule = EarlyStopRule(plateau_count=0)
        assert rule.plateau_count == PLATEAU_COUNT

    def test_invalid_marginal_gain_threshold(self):
        rule = EarlyStopRule(marginal_gain_threshold=-0.1)
        assert rule.marginal_gain_threshold == MARGINAL_GAIN_THRESHOLD


class TestShouldStop:
    def test_empty_history(self):
        stop, reason = should_stop("sec1", [], EarlyStopRule())
        assert stop is False
        assert reason == ""

    def test_single_entry(self):
        stop, reason = should_stop("sec1", [0.5], EarlyStopRule())
        assert stop is False
        assert reason == ""

    def test_quality_reached(self):
        stop, reason = should_stop("sec1", [0.80, 0.96], EarlyStopRule())
        assert stop is True
        assert reason == "quality_reached"

    def test_plateau(self):
        # plateau_count=3: 直近4エントリが非増加 → plateau が diminishing_returns より先
        history = [0.60, 0.75, 0.75, 0.75, 0.75]
        stop, reason = should_stop("sec1", history, EarlyStopRule())
        assert stop is True
        assert reason == "plateau"

    def test_diminishing_returns(self):
        history = [0.80, 0.805]
        stop, reason = should_stop("sec1", history, EarlyStopRule())
        assert stop is True
        assert reason == "diminishing_returns"

    def test_budget_reached(self):
        rule = EarlyStopRule(budget_limit=100)
        stop, reason = should_stop("sec1", [0.5, 0.6], rule, cumulative_cost=100)
        assert stop is True
        assert reason == "budget_reached"

    def test_budget_limit_none(self):
        rule = EarlyStopRule(budget_limit=None)
        # gain=0.1 > threshold, score < 0.95 -> should not stop
        stop, reason = should_stop("sec1", [0.5, 0.6], rule, cumulative_cost=9999)
        assert stop is False
        assert reason == ""

    def test_exception_returns_false(self):
        rule = EarlyStopRule()
        with patch(
            "early_stopping.math.isfinite",
            side_effect=RuntimeError("boom"),
        ):
            stop, reason = should_stop("sec1", [0.5, 0.6], rule)
        assert stop is False
        assert reason == ""

    def test_nan_in_history(self):
        history = [0.5, float("nan"), 0.96]
        stop, reason = should_stop("sec1", history, EarlyStopRule())
        assert stop is True
        assert reason == "quality_reached"

    def test_no_stop_when_improving(self):
        history = [0.70, 0.75]
        stop, reason = should_stop("sec1", history, EarlyStopRule())
        assert stop is False
        assert reason == ""
