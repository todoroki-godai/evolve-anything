"""#135 CLI 配線テスト: `evolve --drain` が subagent_traces ingest と
last_run_timestamp 前進を apply 境界で実効化する。

根因（#135）: `run_evolve(dry_run=False)` に到達する標準経路が存在せず、
phases_capture の `if not dry_run:` 配下（subagent_traces 増分 ingest /
last_run_timestamp 保存）が構造的に死蔵していた。前者は代替経路ゼロで全PJ停滞
（唯一の実害）、後者は #136 の時間フィルタ死亡の直接原因。本テストは main() の
--drain 分岐が weak_signals #484 / reward_ema #64 / queue_state #79 と同型に
両者を drain 境界で発火させ、サマリに surface し、例外を握り潰すことを固定する。

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


def _stub_drain_neighbors(monkeypatch):
    """テスト対象以外の drain persist を無害な固定値へ差し替える。

    drain_pending / weak_signals / reward_ema / queue_state を stub し、
    テスト対象（subagent_traces or last_run）だけを実地で発火させる。
    """
    import evolve_decisions as ed
    from weak_signals import batch as ws_batch
    from audit import reward_ema as re
    from fleet import queue_state as qs

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


def test_drain_branch_ingests_subagent_traces(monkeypatch, capsys):
    """main() の --drain 分岐は subagent_traces を増分 ingest し結果を surface する。"""
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(_state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False})

    calls = {}

    def _fake_ingest(**kw):
        calls["called"] = True
        calls["progress"] = kw.get("progress")
        return {"ingested": 3, "skipped": 1, "capped": False, "remaining": 0}

    monkeypatch.setattr(st_ingest, "ingest_all_projects", _fake_ingest)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    # apply 境界の subagent_traces 増分 ingest が配線済み（唯一の実害の根治）。
    assert calls["called"]
    assert out["subagent_traces_ingest"]["ingested"] == 3
    assert out["subagent_traces_ingest"]["skipped"] == 1
    assert out["subagent_traces_ingest"]["capped"] is False
    assert out["subagent_traces_ingest"]["remaining"] == 0


def test_drain_branch_swallows_subagent_traces_error(monkeypatch, capsys):
    """subagent_traces ingest が失敗しても drain 本体は完走し error を surface する。"""
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(_state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False})

    def _boom(**kw):
        raise RuntimeError("traces store unreadable")

    monkeypatch.setattr(st_ingest, "ingest_all_projects", _boom)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["subagent_traces_ingest"]
    assert "traces store unreadable" in out["subagent_traces_ingest"]["error"]
    # drain 本体（他 persist）は完走している。
    assert "weak_signals_persisted" in out


def test_drain_branch_surfaces_last_run_persisted(monkeypatch, capsys):
    """main() の --drain 分岐は persist_last_run_timestamp を呼び結果を surface する。"""
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(
        st_ingest, "ingest_all_projects",
        lambda **kw: {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0},
    )

    calls = {}

    def _fake_persist(**kw):
        calls["called"] = True
        return {"written": 1, "last_run_timestamp": "2026-07-03T00:00:00+00:00", "dry_run": False}

    monkeypatch.setattr(_state, "persist_last_run_timestamp", _fake_persist)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert calls["called"]
    assert out["last_run_persisted"]["written"] == 1
    assert out["last_run_persisted"]["dry_run"] is False


def test_drain_branch_swallows_last_run_error(monkeypatch, capsys):
    """last_run 永続化が失敗しても drain 本体は完走し error を surface する。"""
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(
        st_ingest, "ingest_all_projects",
        lambda **kw: {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0},
    )

    def _boom(**kw):
        raise RuntimeError("state file unwritable")

    monkeypatch.setattr(_state, "persist_last_run_timestamp", _boom)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["last_run_persisted"]
    assert "state file unwritable" in out["last_run_persisted"]["error"]


def test_drain_advances_last_run_timestamp_end_to_end(monkeypatch, capsys):
    """`evolve --drain` 完走後に evolve-state.json の last_run_timestamp が実書込される。

    #136 の時間フィルタ死亡（永久未書込）の根治を state ファイル差分で固定する。
    persist は stub せず実地で走らせ、他 persist だけ stub する。
    """
    from subagent_traces import ingest as st_ingest

    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(
        st_ingest, "ingest_all_projects",
        lambda **kw: {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0},
    )
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    # 隔離済み state ファイル（conftest が evolve.EVOLVE_STATE_FILE を tmp_path に rebase）。
    state_file = evolve.EVOLVE_STATE_FILE
    assert not state_file.exists() or "last_run_timestamp" not in json.loads(
        state_file.read_text(encoding="utf-8")
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["last_run_persisted"]["written"] == 1

    # state ファイルに last_run_timestamp が実書込された（死蔵の解消）。
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert "last_run_timestamp" in saved
    assert saved["last_run_timestamp"] == out["last_run_persisted"]["last_run_timestamp"]


def test_persist_last_run_timestamp_dry_run_writes_nothing(monkeypatch):
    """persist_last_run_timestamp(dry_run=True) は state に一切触れない。"""
    from evolve import _state

    state_file = evolve.EVOLVE_STATE_FILE
    res = _state.persist_last_run_timestamp(dry_run=True)

    assert res["written"] == 0
    assert res["dry_run"] is True
    assert "last_run_timestamp" in res
    # dry-run は state ファイルを作らない（純度契約・pitfall_dryrun_stateful_store_write）。
    assert not state_file.exists()


def test_persist_last_run_timestamp_preserves_other_keys():
    """既存 state の他キーを保ったまま last_run_timestamp だけ前進させる。"""
    from evolve import _state

    _state.save_evolve_state({"trigger_history": [{"reason": "x"}], "last_run_timestamp": "2020-01-01T00:00:00+00:00"})
    res = _state.persist_last_run_timestamp(ts="2026-07-03T12:00:00+00:00")

    assert res["written"] == 1
    saved = _state.load_evolve_state()
    assert saved["last_run_timestamp"] == "2026-07-03T12:00:00+00:00"
    # 他キーは保たれる。
    assert saved["trigger_history"] == [{"reason": "x"}]
