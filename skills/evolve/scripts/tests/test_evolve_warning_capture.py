"""evolve.py の警告キャプチャ配線テスト（#341）。

scipy RuntimeWarning(NaN, #340) 等の「phase が throw しない警告」は phase.error に
乗らず stderr に流れて消えていた。`_capture_warnings` がフェーズ実行中の警告を sink に
シリアライズし、self_analysis が result["warnings"] を読んで surface できるようにする。

ここでは決定論で `_capture_warnings` の捕捉契約のみを検証する（重い run_evolve 全体や
LLM は回さない / scipy 実依存もしない — warnings.warn で代用）。
"""
import sys
import warnings
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS.parent.parent.parent / "scripts" / "lib"))

import evolve  # noqa: E402
from evolve_introspect import analyze_evolve_result  # noqa: E402


def test_capture_warnings_records_runtime_warning():
    sink = []
    with evolve._capture_warnings(sink):
        warnings.warn("invalid value encountered in divide", RuntimeWarning)
    assert len(sink) == 1
    rec = sink[0]
    assert rec["category"] == "RuntimeWarning"
    assert "invalid value" in rec["message"]
    assert "filename" in rec and "lineno" in rec


def test_capture_warnings_empty_when_no_warning():
    sink = []
    with evolve._capture_warnings(sink):
        pass
    assert sink == []


def test_capture_warnings_does_not_swallow_exceptions():
    sink = []
    raised = False
    try:
        with evolve._capture_warnings(sink):
            warnings.warn("boom", RuntimeWarning)
            raise ValueError("real error")
    except ValueError:
        raised = True
    assert raised is True
    # 例外で抜けても、それまでに出た警告は記録される。
    assert len(sink) == 1


def test_captured_warning_surfaces_in_self_analysis():
    """sink の中身を result["warnings"] に入れると self_analysis が候補化する（契約 E2E）。"""
    sink = []
    with evolve._capture_warnings(sink):
        warnings.warn("invalid value encountered in sqrt", RuntimeWarning)
    result = {"phases": {}, "warnings": sink}
    analysis = analyze_evolve_result(result)
    cands = analysis["runtime_errors"]["candidates"]
    assert any(c["dedup_key"].startswith("runtime_warning:") for c in cands)
