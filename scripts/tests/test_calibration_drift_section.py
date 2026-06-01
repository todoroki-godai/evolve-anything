"""calibration drift observability builder のテスト（#286・決定論）。

accept/reject 履歴の有無・件数・相関 drift で section が
None / データ不足 / ✓ / ⚠ を返すことを検証する。load_history と
analyze_correlations を monkeypatch し、builder のルーティングのみを検証する。
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
_FE = _PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"
for _p in (_LIB, _SCRIPTS, _FE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import fitness_evolution  # noqa: E402
from audit.observability import _OBSERVABILITY_BUILDERS  # noqa: E402
from audit.sections import build_calibration_drift_section  # noqa: E402


def _records(n: int):
    """best_fitness と human_accepted が揃った有効レコードを n 件返す。"""
    return [
        {"fitness_func": "skill_quality", "best_fitness": 0.7, "human_accepted": True}
        for _ in range(n)
    ]


def test_none_when_no_history(monkeypatch, tmp_path):
    """accept/reject 履歴が無ければ対象外（None）。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: [])
    assert build_calibration_drift_section(tmp_path) is None


def test_data_insufficient_line(monkeypatch, tmp_path):
    """30 件未満なら『データ不足 N/30』を評価済として残す。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: _records(10))
    section = build_calibration_drift_section(tmp_path)
    combined = "\n".join(section)
    assert "Fitness Calibration Drift" in combined
    assert "データ不足 10/30" in combined


def test_clean_when_no_drift(monkeypatch, tmp_path):
    """十分なデータで drift（warning）が無ければ ✓ 行を残す。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: _records(30))
    monkeypatch.setattr(
        fitness_evolution,
        "analyze_correlations",
        lambda h: {"by_fitness_func": {"skill_quality": {"correlation": 0.8}}},
    )
    section = build_calibration_drift_section(tmp_path)
    combined = "\n".join(section)
    assert "✓" in combined
    assert "drift なし" in combined


def test_warn_when_drift(monkeypatch, tmp_path):
    """相関低下（warning あり）の fitness_func を ⚠ で advisory 提示する。"""
    monkeypatch.setattr(fitness_evolution, "load_history", lambda *a, **k: _records(30))
    monkeypatch.setattr(
        fitness_evolution,
        "analyze_correlations",
        lambda h: {
            "by_fitness_func": {
                "skill_quality": {
                    "correlation": 0.21,
                    "warning": "相関低下",
                }
            }
        },
    )
    section = build_calibration_drift_section(tmp_path)
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "evolve-fitness" in combined
    assert "skill_quality" in combined
    assert "0.21" in combined
    # 人間承認 MUST が明記されている
    assert "人間承認" in combined


def test_registered_in_observability_contract():
    """calibration_drift が _OBSERVABILITY_BUILDERS に登録されている。"""
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "calibration_drift" in keys
