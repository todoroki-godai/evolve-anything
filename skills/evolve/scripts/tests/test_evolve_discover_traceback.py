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


def test_discover_full_crash_sets_degraded_reflect_count(tmp_path, monkeypatch):
    """run_discover が全クラッシュしても discover フェーズに degraded sentinel
    reflect_data_count == -1 を残す（#32）。

    discover が例外で全クラッシュすると phase 出力が `{"error":..., "traceback":...}`
    だけになり `reflect_data_count` キー自体が欠落 → 下流が `.get("reflect_data_count")`
    で None。evolve スキル手順は degraded を sentinel `-1` として扱い `< 0` で判定する
    規定だが、実際は None/欠落になり `None < 0` で二次クラッシュしていた。
    全クラッシュ経路でも degraded sentinel を必ずセットして契約を一本化する。
    """
    fake = type(sys)("discover")

    def _boom(**kwargs):
        raise RuntimeError("discover boom")

    fake.run_discover = _boom
    monkeypatch.setitem(sys.modules, "discover", fake)

    live_audit = sys.modules.get("audit", audit)
    with mock.patch.object(live_audit, "run_audit", return_value="## audit report"):
        result = run_evolve(project_dir=str(tmp_path), dry_run=True)

    discover_phase = result["phases"]["discover"]
    # 全クラッシュでも degraded sentinel -1（int）で契約を一本化する
    assert discover_phase.get("reflect_data_count") == -1
    assert isinstance(discover_phase["reflect_data_count"], int)
    # クラッシュ自体の観測可能性は #521 の契約どおり維持する
    assert discover_phase["error"] == "discover boom"
    assert "traceback" in discover_phase


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
