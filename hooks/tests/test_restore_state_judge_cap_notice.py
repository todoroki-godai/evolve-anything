"""restore_state の llm_judge 日次上限到達通知（#408）。

daily runner（evolve-daily-run）が evolve-queue.json に埋め込んだ ``llm_judge.capped``
を SessionStart で systemMessage（ADR-038 = user 向けチャネル）として surface する。
新ストアは作らず既存の evolve-queue.json（#80）を再利用する。

- 上限到達（capped=True）→ systemMessage が出る
- 上限未到達 / llm_judge フィールド無し / ファイル無し → 沈黙（stdout を汚さない）

env ガード: install レイアウト env のときだけ実環境 DATA_DIR を読む（queue notice と同型）。
書き込み先は tmp_path のみ。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402


def _fresh_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _capped_queue() -> dict:
    return {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 10,
        "queue": [],
        "llm_judge": {
            "unjudged_before": 250,
            "selected": 200,
            "capped": True,
            "corrections": 5,
            "call_failed": 0,
        },
    }


def _not_capped_queue() -> dict:
    return {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 10,
        "queue": [],
        "llm_judge": {
            "unjudged_before": 3,
            "selected": 3,
            "capped": False,
            "corrections": 1,
            "call_failed": 0,
        },
    }


def _source_failed_queue() -> dict:
    return {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 10,
        "queue": [],
        "llm_judge": {
            "unjudged_before": 0,
            "selected": 0,
            "capped": False,
            "corrections": 0,
            "call_failed": 0,
            "source_failed": True,
            "source_error": "RuntimeError: duckdb schema mismatch",
        },
    }


def _write_queue(data_dir: Path, payload: dict) -> None:
    (data_dir / "evolve-queue.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_env(tmp_path, monkeypatch):
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return source


def test_deliver_fires_when_capped(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _capped_queue())
    restore_state._deliver_judge_cap_notice()
    out = capsys.readouterr().out
    assert out
    payload = json.loads(out.strip())
    assert "systemMessage" in payload
    assert "200" in payload["systemMessage"]


def test_deliver_fires_when_source_failed(tmp_path, monkeypatch, capsys):
    """#410 [Must]E: capped=False でも source_failed=True なら沈黙しない。"""
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _source_failed_queue())
    restore_state._deliver_judge_cap_notice()
    out = capsys.readouterr().out
    assert out
    payload = json.loads(out.strip())
    assert "duckdb schema mismatch" in payload["systemMessage"]


def test_deliver_silent_when_not_capped(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _not_capped_queue())
    restore_state._deliver_judge_cap_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_when_no_queue_file(tmp_path, monkeypatch, capsys):
    _install_env(tmp_path, monkeypatch)
    restore_state._deliver_judge_cap_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    restore_state._deliver_judge_cap_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_env(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    restore_state._deliver_judge_cap_notice()
    assert capsys.readouterr().out == ""


def test_deliver_does_not_write(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _capped_queue())
    before = {p.name for p in source.iterdir()}
    restore_state._deliver_judge_cap_notice()
    after = {p.name for p in source.iterdir()}
    assert before == after  # 通知は read-only


def test_handle_session_start_invokes_judge_cap_notice(tmp_path, monkeypatch, capsys):
    """handle_session_start が judge cap 通知を配信フローに含む（配線回帰）。"""
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _capped_queue())
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "200" in out
