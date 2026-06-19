"""calibration drift の trigger_engine proactive 提案テスト（#286・決定論）。

evaluate_session_end が accept/reject >= 30 かつ drift 検出時に
/evolve-anything:evolve-fitness を action に含めること、データ不足・drift なしでは
発火しないことを検証する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_FE = _PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"
for _p in (_LIB, _FE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import fitness_evolution  # noqa: E402
from trigger_engine import session_corrections  # noqa: E402


def _records(n: int, accepted: bool = True):
    return [
        {"fitness_func": "skill_quality", "best_fitness": 0.7, "human_accepted": accepted}
        for _ in range(n)
    ]


# ── _detect_calibration_drift 直接 ─────────────────────────────────────────


def test_detect_returns_funcs_when_drift(monkeypatch):
    """30件以上 かつ drift で func 名リストを返す。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: _records(30))
    monkeypatch.setattr(
        fitness_evolution,
        "detect_drifted_funcs",
        lambda h: {"valid_count": 30, "sufficient": True,
                   "drifted": [{"func": "skill_quality", "correlation": 0.1}]},
    )
    assert session_corrections._detect_calibration_drift() == ["skill_quality"]


def test_detect_none_when_insufficient(monkeypatch):
    """30件未満では None（発火しない）。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: _records(10))
    monkeypatch.setattr(
        fitness_evolution,
        "detect_drifted_funcs",
        lambda h: {"valid_count": 10, "sufficient": False, "drifted": []},
    )
    assert session_corrections._detect_calibration_drift() is None


def test_detect_none_when_no_drift(monkeypatch):
    """十分なデータでも drift が無ければ None。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: _records(30))
    monkeypatch.setattr(
        fitness_evolution,
        "detect_drifted_funcs",
        lambda h: {"valid_count": 30, "sufficient": True, "drifted": []},
    )
    assert session_corrections._detect_calibration_drift() is None


# ── evaluate_session_end 経由 ──────────────────────────────────────────────


def _quiet_state():
    """他トリガー（audit/session/days）が発火しない recent state。"""
    now = datetime.now(timezone.utc).isoformat()
    return {"last_run_timestamp": now, "last_audit_timestamp": now}


def test_session_end_includes_evolve_fitness_action(monkeypatch):
    """drift 検出時、action に /evolve-anything:evolve-fitness が入る。"""
    monkeypatch.setattr(
        session_corrections, "_detect_calibration_drift", lambda: ["skill_quality"]
    )
    monkeypatch.setattr(session_corrections, "_save_state", lambda s: None)

    with mock.patch.object(session_corrections, "_count_sessions_since", return_value=0):
        result = session_corrections.evaluate_session_end(state=_quiet_state())

    assert result.triggered is True
    assert "calibration_drift" in result.details.get("all_reasons", [])
    assert "/evolve-anything:evolve-fitness" in result.details.get("all_actions", [])
    assert "人間承認 MUST" in result.message


def test_session_end_no_fire_when_no_drift(monkeypatch):
    """drift なし かつ他条件未達なら triggered=False。"""
    monkeypatch.setattr(session_corrections, "_detect_calibration_drift", lambda: None)
    monkeypatch.setattr(session_corrections, "_save_state", lambda s: None)

    with mock.patch.object(session_corrections, "_count_sessions_since", return_value=0):
        result = session_corrections.evaluate_session_end(state=_quiet_state())

    assert result.triggered is False
