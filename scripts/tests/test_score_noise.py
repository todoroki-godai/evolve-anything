"""score_noise.py の統計関数および ConfidenceInterval スキーマのテスト。"""
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_LIB_DIR = _SCRIPTS_DIR / "lib"
for _p in [str(_SCRIPTS_DIR), str(_LIB_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from score_noise import compute_stats, to_confidence_interval
from scorer_schema import ConfidenceInterval


# ──────────────────────────────────────────────────────
# test_confidence_interval_schema_fields
# ──────────────────────────────────────────────────────

def test_confidence_interval_schema_fields():
    """ConfidenceInterval の全フィールドが存在し型が正しい。"""
    ci = ConfidenceInterval(mean=0.7, std=0.05, lower=0.65, upper=0.75, n=5)
    assert hasattr(ci, "mean")
    assert hasattr(ci, "std")
    assert hasattr(ci, "lower")
    assert hasattr(ci, "upper")
    assert hasattr(ci, "n")
    assert isinstance(ci.mean, float)
    assert isinstance(ci.std, float)
    assert isinstance(ci.lower, float)
    assert isinstance(ci.upper, float)
    assert isinstance(ci.n, int)


# ──────────────────────────────────────────────────────
# test_confidence_interval_multi_run
# ──────────────────────────────────────────────────────

def test_confidence_interval_multi_run():
    """複数スコアから mean/std/lower/upper が正しく計算される。"""
    scores = [0.6, 0.7, 0.8, 0.9, 0.5]
    stats = compute_stats(scores)
    ci = to_confidence_interval(stats)

    assert ci.n == 5
    assert abs(ci.mean - stats["mean"]) < 1e-6
    assert abs(ci.std - stats["std"]) < 1e-6
    # lower = mean - std, upper = mean + std
    assert abs(ci.lower - (stats["mean"] - stats["std"])) < 1e-6
    assert abs(ci.upper - (stats["mean"] + stats["std"])) < 1e-6
    # lower <= mean <= upper
    assert ci.lower <= ci.mean <= ci.upper


# ──────────────────────────────────────────────────────
# test_confidence_interval_single_run
# ──────────────────────────────────────────────────────

def test_confidence_interval_single_run():
    """1件の場合 std=0.0、lower == upper == mean、n=1。"""
    scores = [0.75]
    stats = compute_stats(scores)
    ci = to_confidence_interval(stats)

    assert ci.n == 1
    assert ci.std == 0.0
    assert ci.lower == ci.mean
    assert ci.upper == ci.mean
    assert abs(ci.mean - 0.75) < 1e-6


# ──────────────────────────────────────────────────────
# 追加: compute_stats の基本動作確認
# ──────────────────────────────────────────────────────

def test_compute_stats_multi():
    """compute_stats が mean/std/min/max/n を返す。"""
    scores = [0.4, 0.6, 0.8]
    stats = compute_stats(scores)
    assert stats["n"] == 3
    assert abs(stats["mean"] - 0.6) < 1e-3
    assert stats["std"] > 0.0
    assert stats["min"] == pytest.approx(0.4, abs=1e-4)
    assert stats["max"] == pytest.approx(0.8, abs=1e-4)


def test_compute_stats_single():
    """compute_stats が 1 件のとき std=0.0 を返す。"""
    stats = compute_stats([0.5])
    assert stats["n"] == 1
    assert stats["std"] == 0.0
    assert stats["mean"] == pytest.approx(0.5, abs=1e-4)
