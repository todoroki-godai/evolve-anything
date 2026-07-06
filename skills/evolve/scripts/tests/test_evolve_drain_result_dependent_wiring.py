"""#146 CLI 配線テスト: `evolve --drain --result-json <path>` が result 依存3項目
（calibration state / tool_usage_snapshot / growth crystallization）を apply 境界で
実効化する（ADR-051）。

根因（#146 / #135）: dry-run→drain の標準フローは run_evolve(dry_run=False) に到達せず、
phases_capture の `if not dry_run:` 配下（calibration/tool_usage/growth）が構造的に死蔵し、
較正トレンド・tool 使用トレンド・成長結晶化が標準フローで永久に貯まらなかった。本テストは
main() の --drain 分岐が dry-run の `--output` result JSON を読んで値を運搬し3項目を確定し、
result-json 欠落/不読時は graceful skip して他 persist を完走することを固定する。

HOME / DATA_DIR 隔離はルート conftest（#457/#119）が autouse で行う。
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS.parent.parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve  # noqa: E402


def _full_result() -> dict:
    """dry-run が `--output` に書く形の full result（self_evolution + discover を含む）。"""
    return {
        "generated_at": "2026-07-06T00:00:00+00:00",
        "phases": {
            "self_evolution": {
                "calibrations": {"skill_a": {"threshold": 0.5}},
                "proposals": [{"skill": "skill_a"}, {"skill": "skill_b"}],
            },
            "discover": {
                "tool_usage_patterns": {
                    "builtin_replaceable": [{"count": 3}, {"count": 2}],
                    "repeating_patterns": [
                        {"pattern": "sleep 5", "count": 4},
                        {"pattern": "git status", "count": 1},
                    ],
                    "bash_calls": 10,
                    "total_tool_calls": 40,
                },
            },
            "remediation": {"classified": {"auto_fixable": [], "proposable": []}},
        },
    }


def _stub_drain_neighbors(monkeypatch):
    """テスト対象以外の drain persist を無害な固定値へ差し替える。

    drain_pending / weak_signals / reward_ema / queue_state / subagent_traces /
    last_run を stub し、テスト対象（result 依存3項目）だけを実地で発火させる。
    session_store.ingest / clear_snooze は隔離 DATA_DIR 上で無害に走るので stub しない。
    """
    import evolve_decisions as ed
    from weak_signals import batch as ws_batch
    from audit import reward_ema as re
    from fleet import queue_state as qs
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )
    monkeypatch.setattr(
        ws_batch, "persist_weak_signals_drain",
        lambda slug, **kw: {"written": 0, "dry_run": False},
    )
    monkeypatch.setattr(
        re, "persist_reward_ema_batch", lambda project_dir, **kw: {"persisted": 0}
    )
    monkeypatch.setattr(
        qs, "persist_last_evolve", lambda slug, **kw: {"written": 0, "dry_run": False}
    )
    monkeypatch.setattr(
        st_ingest, "ingest_all_projects",
        lambda **kw: {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0},
    )
    monkeypatch.setattr(
        _state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False}
    )


def test_drain_persists_result_dependent_items(monkeypatch, capsys, tmp_path):
    """--drain --result-json 有りで calibration/tool_usage が state に、growth が journal に確定する。"""
    import growth_journal

    _stub_drain_neighbors(monkeypatch)

    emitted: dict = {}
    monkeypatch.setattr(
        growth_journal, "emit_crystallization", lambda **kw: emitted.update(kw)
    )

    rj = tmp_path / "result.json"
    rj.write_text(json.dumps(_full_result()), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--result-json", str(rj)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["result_state_persisted"]["calibration_written"] is True
    assert out["result_state_persisted"]["tool_usage_written"] is True
    assert out["growth_crystallized"] is True

    # グローバル state ファイルに実書込された（時刻は drain 時点・中身は result 由来）。
    state = json.loads(evolve.EVOLVE_STATE_FILE.read_text(encoding="utf-8"))
    last = state["calibration_history"][-1]
    assert last["calibrations"] == {"skill_a": {"threshold": 0.5}}
    assert last["proposals_count"] == 2
    assert state["last_calibration_timestamp"]
    snap = state["tool_usage_snapshot"]
    assert snap["builtin_replaceable"] == 5
    assert snap["sleep_patterns"] == 4
    assert snap["bash_ratio"] == 0.25

    # growth crystallization が evolve source で発火した。
    assert emitted.get("source") == "evolve"


def test_drain_without_result_json_skips_gracefully(monkeypatch, capsys):
    """--result-json 無しでは3項目 skip・growth 未発火だが他 persist は継続する。"""
    import growth_journal

    _stub_drain_neighbors(monkeypatch)

    called: dict = {}
    monkeypatch.setattr(
        growth_journal, "emit_crystallization",
        lambda **kw: called.__setitem__("emitted", True),
    )

    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["result_state_persisted"] == {"skipped": "no_result_json"}
    assert out["growth_crystallized"] == {"skipped": "no_result_json"}
    assert not called  # growth crystallization は発火しない
    # result 非依存 persist は無傷（drain 本体を完走）。
    assert "weak_signals_persisted" in out
    assert out["snooze_cleared"] is True


def test_drain_with_missing_result_json_skips_gracefully(monkeypatch, capsys, tmp_path):
    """存在しない --result-json パスでも skip 理由を surface し他 persist は継続する。"""
    _stub_drain_neighbors(monkeypatch)

    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--result-json", str(missing)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["result_state_persisted"] == {"skipped": "result_json_not_found"}
    assert out["growth_crystallized"] == {"skipped": "result_json_not_found"}
    assert "weak_signals_persisted" in out


def test_persist_result_dependent_state_dry_run_writes_nothing(tmp_path):
    """dry_run=True は state を一切書かない（#491 契約・drain 前の分析は書込ゼロ）。"""
    from evolve._state import persist_result_dependent_state

    res = persist_result_dependent_state(_full_result(), dry_run=True)
    assert res["written"] == 0
    assert res["dry_run"] is True
    assert not evolve.EVOLVE_STATE_FILE.exists()


def test_persist_result_dependent_state_dedup_double_append(tmp_path):
    """同一 result で2回呼んでも calibration_history は二重 append されない（直接実行→drain の両走）。"""
    from evolve._state import persist_result_dependent_state

    r = _full_result()
    first = persist_result_dependent_state(r)
    second = persist_result_dependent_state(r)

    assert first["calibration_written"] is True
    assert second["calibration_written"] is False
    assert second["calibration_deduped"] is True

    state = json.loads(evolve.EVOLVE_STATE_FILE.read_text(encoding="utf-8"))
    assert len(state["calibration_history"]) == 1  # 二重 append 回避
    assert "tool_usage_snapshot" in state  # upsert なので存在


def test_dry_run_mode_does_not_persist_result_dependent(monkeypatch, capsys):
    """`--dry-run`（非 --drain）モードでは result 依存3項目 persist を一切呼ばない。"""
    from evolve import _state as state_mod
    from evolve import _report as report_mod

    calls = {"state": 0, "growth": 0}
    monkeypatch.setattr(
        state_mod, "persist_result_dependent_state",
        lambda *a, **k: calls.__setitem__("state", calls["state"] + 1) or {},
    )
    monkeypatch.setattr(
        report_mod, "_emit_growth_crystallization",
        lambda *a, **k: calls.__setitem__("growth", calls["growth"] + 1),
    )
    monkeypatch.setattr(evolve, "run_evolve", lambda **kw: {"phases": {}})
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run", "--project-dir", "/tmp/whatever"])

    evolve.main()

    assert calls["state"] == 0
    assert calls["growth"] == 0
