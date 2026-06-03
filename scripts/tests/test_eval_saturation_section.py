"""eval_saturation observability builder のテスト（#292・決定論）。

eval-sets の有無・飽和有無で section が None / ✓ / ⚠ を返すこと、
observability contract（markdown ↔ 構造化の両経路伝播）に登録されていることを検証する。
eval_saturation.compute_eval_saturation を monkeypatch で固定し、builder の整形を検証。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import eval_saturation  # noqa: E402
from audit.observability import _OBSERVABILITY_BUILDERS, collect_observability  # noqa: E402
from audit.sections_eval import build_eval_saturation_section  # noqa: E402


def _patch(monkeypatch, result):
    monkeypatch.setattr(
        eval_saturation, "compute_eval_saturation", lambda **kw: result
    )


def test_none_when_not_applicable(tmp_path, monkeypatch):
    """eval set が無い環境 → 対象外（None）。"""
    _patch(monkeypatch, {"applicable": False, "evaluated": 0, "saturated": []})
    assert build_eval_saturation_section(tmp_path) is None


def test_clean_line_when_no_saturation(tmp_path, monkeypatch):
    """飽和なし → 評価済 ✓ 行を残す（silence != evaluated）。"""
    _patch(monkeypatch, {"applicable": True, "evaluated": 5, "saturated": []})
    section = build_eval_saturation_section(tmp_path)
    combined = "\n".join(section)
    assert "Eval Saturation" in combined
    assert "✓" in combined
    assert "飽和兆候なし" in combined


def test_warn_line_with_saturation(tmp_path, monkeypatch):
    """飽和あり → ⚠ で対象スキルと理由を surface。"""
    _patch(monkeypatch, {
        "applicable": True,
        "evaluated": 3,
        "saturated": [
            {"skill": "posheavy", "total": 11, "negative_ratio": 0.09,
             "reasons": ["low_negative_coverage"]},
        ],
    })
    section = build_eval_saturation_section(tmp_path)
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "posheavy" in combined
    assert eval_saturation.REASON_LABELS["low_negative_coverage"] in combined


def test_registered_in_observability_contract():
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "eval_saturation" in keys
    # calibration_drift の直後（同セクション帯）に同居する（#292 要件）
    assert keys.index("eval_saturation") == keys.index("calibration_drift") + 1


def test_collect_observability_surfaces_eval_saturation(tmp_path, monkeypatch):
    """collect_observability 経由でも eval_saturation key が立つ（両経路伝播）。"""
    _patch(monkeypatch, {"applicable": True, "evaluated": 2, "saturated": []})
    result = collect_observability(tmp_path)
    assert "eval_saturation" in result
