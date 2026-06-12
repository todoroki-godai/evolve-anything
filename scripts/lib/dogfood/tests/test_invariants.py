"""dogfood.invariants のユニットテスト（#496 Layer 2）。

result JSON の機械検査。合成 fixture のみ。実環境は読まない。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402

from dogfood import invariants  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    isolate_home(monkeypatch, tmp_path / "_home")


def _ok_result() -> dict:
    """全 invariant を満たす最小 result。"""
    return {
        "phases": {"observe": {}, "audit": {"report": "..."}},
        "observability": {"weak_signals": ["✓ ..."]},
        "growth_report": {"ok": True},
        "correction_semantic": {"phase": "daily_review"},
        "weak_signals": {"promoted": 0, "expired": 1},
    }


# --- 必須 top-level キーの存在 -------------------------------------------------

def test_required_keys_present_passes():
    failures = invariants.check_required_keys(_ok_result())
    assert failures == []


def test_required_keys_missing_detected():
    r = _ok_result()
    del r["observability"]
    del r["growth_report"]
    failures = invariants.check_required_keys(r)
    msgs = " ".join(f["detail"] for f in failures)
    assert "observability" in msgs
    assert "growth_report" in msgs


def test_required_keys_error_value_counts_as_failure():
    # キーは存在するが値が {"error": ...} のみ → 成功時不変条件を満たさない
    r = _ok_result()
    r["observability"] = {"error": "boom"}
    failures = invariants.check_required_keys(r)
    assert any("observability" in f["detail"] for f in failures)


# --- 件数フィールド非負 --------------------------------------------------------

def test_non_negative_counts_passes():
    failures = invariants.check_non_negative_counts(_ok_result())
    assert failures == []


def test_non_negative_counts_detects_negative():
    r = _ok_result()
    r["weak_signals"] = {"promoted": -3}
    failures = invariants.check_non_negative_counts(r)
    assert any("promoted" in f["detail"] and "-3" in f["detail"] for f in failures)


def test_non_negative_counts_nested():
    r = _ok_result()
    r["phases"]["skill_evolve"] = {"counts": {"applied": -1}}
    failures = invariants.check_non_negative_counts(r)
    assert any("applied" in f["detail"] for f in failures)


# --- 当PJスコープ ≤ 全PJスコープ ----------------------------------------------

def test_pj_le_global_passes():
    r = {"foo_count": 3, "foo_count_all_pj": 10}
    failures = invariants.check_pj_le_global(r)
    assert failures == []


def test_pj_le_global_violation_detected():
    r = {"foo_count": 12, "foo_count_all_pj": 10}
    failures = invariants.check_pj_le_global(r)
    assert any("foo_count" in f["detail"] for f in failures)


def test_pj_le_global_no_pair_no_failure():
    # 片方しか無い → 検査対象外（FP を出さない）
    r = {"foo_count": 12}
    assert invariants.check_pj_le_global(r) == []


# --- observability contract 突合 ----------------------------------------------

def test_observability_contract_keys_subset(monkeypatch):
    # contract が知る builder key の一部だけが result に出ているのは正常
    r = _ok_result()
    failures = invariants.check_observability_contract(r)
    # weak_signals は実 contract に存在する key なので unknown 判定されない
    assert all("weak_signals" not in f["detail"] for f in failures)


def test_observability_contract_unknown_key_detected():
    r = _ok_result()
    r["observability"] = {"not_a_real_builder_key": ["x"]}
    failures = invariants.check_observability_contract(r)
    assert any("not_a_real_builder_key" in f["detail"] for f in failures)


# --- run_all 集約 --------------------------------------------------------------

def test_run_all_green_on_ok_result():
    results = invariants.run_all(_ok_result())
    assert all(not chk["failures"] for chk in results), results


def test_run_all_collects_failures():
    r = _ok_result()
    del r["growth_report"]
    r["weak_signals"] = {"promoted": -1}
    results = invariants.run_all(r)
    total = sum(len(chk["failures"]) for chk in results)
    assert total >= 2
