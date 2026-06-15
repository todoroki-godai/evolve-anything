"""#521: discover フェーズが例外で落ちても traceback を捨てず result に残す。

旧コードは Phase 2: Discover の except で `{"error": str(e)}` だけを残し traceback を
捨てていた。run_discover 内の try/except 外 subscript が None で落ちると root cause が
永久に観測不能になり、result は緑に見えた（self_analysis も手掛かりを得られない）。

本テストは run_discover を例外で落とし、discover フェーズに `error` と `traceback`
（呼び出し元の行を含む）が残ることを検証する。

TDD-first: traceback 捕捉実装の前にこのテストを書いている。
"""
import sys
from pathlib import Path
from unittest import mock

_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

import audit  # noqa: E402
from evolve import run_evolve  # noqa: E402


def test_discover_phase_captures_traceback_on_failure(tmp_path, monkeypatch):
    """run_discover が例外でも discover フェーズに error + traceback を残す。"""
    # run_discover を例外に差し替える（monkeypatch.setitem で後続テストへの汚染を防ぐ）。
    fake = type(sys)("discover")

    def _boom(**kwargs):
        raise RuntimeError("discover boom")

    fake.run_discover = _boom
    monkeypatch.setitem(sys.modules, "discover", fake)

    # audit は重いので mock してテストを軽くする（discover フェーズの検証が目的）。
    live_audit = sys.modules.get("audit", audit)
    with mock.patch.object(live_audit, "run_audit", return_value="## audit report"):
        result = run_evolve(project_dir=str(tmp_path), dry_run=True)

    discover_phase = result["phases"]["discover"]
    assert discover_phase["error"] == "discover boom"
    # traceback を捨てない（root cause が観測可能であること）
    assert "traceback" in discover_phase
    assert "RuntimeError" in discover_phase["traceback"]
    assert "discover boom" in discover_phase["traceback"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
