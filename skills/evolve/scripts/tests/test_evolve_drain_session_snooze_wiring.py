"""#150 CLI 配線テスト: `evolve --drain` が session_store.ingest と clear_snooze を
apply 境界で実効化する。

根因（#150 / #415 Phase A）: `run_evolve(dry_run=False)` に到達する標準経路が存在せず、
phases_capture の `if not dry_run:` 配下（session_store.ingest / clear_snooze）が
構造的に死蔵していた。前者は sessions.db が stale・sessions.jsonl が単調肥大する実害、
後者は evolve 完了によるスヌーズ自動解除が効かない。本テストは main() の --drain 分岐が
weak_signals #484 / subagent_traces #135 と同型に両者を drain 境界で発火させ、
サマリに surface し、例外を握り潰す（drain 本体を完走する）ことを固定する。

HOME / DATA_DIR 隔離はルート conftest（#457/#119）が autouse で行う。
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS.parent.parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve  # noqa: E402


def _stub_drain_neighbors(monkeypatch):
    """テスト対象以外の drain persist を無害な固定値へ差し替える。

    drain_pending / weak_signals / reward_ema / queue_state / subagent_traces /
    last_run を stub し、テスト対象（session_store.ingest / clear_snooze）だけを
    実地で発火させる。
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


def test_drain_branch_ingests_sessions(monkeypatch, capsys):
    """main() の --drain 分岐は session_store.ingest を呼び結果を surface する。"""
    import session_store

    _stub_drain_neighbors(monkeypatch)

    calls = {}

    def _fake_ingest():
        calls["called"] = True
        return 5

    monkeypatch.setattr(session_store, "ingest", _fake_ingest)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert calls["called"]
    assert out["sessions_ingested"] == 5


def test_drain_branch_swallows_sessions_ingest_error(monkeypatch, capsys):
    """session ingest が失敗しても drain 本体は完走し error を surface する。"""
    import session_store

    _stub_drain_neighbors(monkeypatch)

    def _boom():
        raise RuntimeError("sessions db locked")

    monkeypatch.setattr(session_store, "ingest", _boom)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["sessions_ingested"]
    assert "sessions db locked" in out["sessions_ingested"]["error"]
    # drain 本体（他 persist）は完走している。
    assert "weak_signals_persisted" in out


def test_drain_branch_clears_snooze(monkeypatch, capsys):
    """main() の --drain 分岐は clear_snooze を呼び snooze_cleared を surface する。"""
    import trigger_engine as te

    _stub_drain_neighbors(monkeypatch)

    calls = {}
    monkeypatch.setattr(te, "clear_snooze", lambda: calls.__setitem__("called", True))
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert calls["called"]
    assert out["snooze_cleared"] is True


def test_drain_branch_swallows_snooze_error(monkeypatch, capsys):
    """snooze 解除が失敗しても drain 本体は完走し error を surface する。"""
    import trigger_engine as te

    _stub_drain_neighbors(monkeypatch)

    def _boom():
        raise RuntimeError("snooze file unwritable")

    monkeypatch.setattr(te, "clear_snooze", _boom)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["snooze_cleared"]
    assert "snooze file unwritable" in out["snooze_cleared"]["error"]
    assert "weak_signals_persisted" in out


def test_drain_ingests_sessions_and_clears_snooze_end_to_end(monkeypatch, capsys, tmp_path):
    """`evolve --drain` 完走で sessions.jsonl→sessions.db 取り込みと snooze marker 削除が実書込される。

    session_store.ingest / clear_snooze は stub せず実地で走らせ、他 persist だけ stub する。
    - sessions.jsonl の 1 レコードが db に取り込まれ、live jsonl が rotate される
    - trigger-snooze.json marker が削除される
    """
    import session_store
    import trigger_engine as te

    if not session_store.HAS_DUCKDB:
        pytest.skip("duckdb 未導入では ingest が noop（sessions.db を作らない）")

    _stub_drain_neighbors(monkeypatch)

    # session_store の解決先を tmp に pin（#137 慣習: _DATA_DIR_OVERRIDE 1 本）。
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_store, "_DATA_DIR_OVERRIDE", data_dir)
    live_jsonl = data_dir / "sessions.jsonl"
    live_jsonl.write_text(
        json.dumps({
            "session_id": "s-150",
            "timestamp": "2026-07-04T00:00:00+00:00",
            "project": "evolve-anything",
            "type": "session_end",
            "skill_count": 1,
            "error_count": 0,
        }) + "\n",
        encoding="utf-8",
    )

    # snooze marker を tmp に置き、clear_snooze の削除対象を pin する。
    snooze_file = data_dir / "trigger-snooze.json"
    snooze_file.write_text(json.dumps({"snoozed_until": "2099-01-01T00:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(te, "SNOOZE_FILE", snooze_file)

    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)

    # sessions ingest: 1 件挿入され live jsonl は rotate（`.ingested-*`）された。
    assert out["sessions_ingested"] == 1
    assert (data_dir / "sessions.db").exists()
    assert not live_jsonl.exists()
    assert list(data_dir.glob("sessions.jsonl.ingested-*"))

    # snooze 解除: marker が削除された。
    assert out["snooze_cleared"] is True
    assert not snooze_file.exists()
