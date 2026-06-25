"""restore_state の evolve-queue 通知（#80 Phase 1b）。

毎朝の `fleet ingest`→`fleet queue` が `evolve-queue.json` に保存した待ち PJ を、
SessionStart で systemMessage（ADR-038 = user 向けチャネル）として surface する。

- queue 有 → systemMessage に待ち PJ 一覧が出る
- 空 queue / ファイル無し → 沈黙（stdout を汚さない）
- stale（generated_at が古い）→ advisory が付く

env ガード: install レイアウト env のときだけ実環境 DATA_DIR を読む（utterance staleness と同型）。
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


SAMPLE_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [
        {
            "pj_slug": "figma-to-code",
            "material_count": 9,
            "weak_unprocessed": 7,
            "new_corrections": 2,
            "last_evolve_at": "2026-06-20T10:00:00Z",
            "activity_since": {"subagents": 40, "sessions": 5},
            "reason": "weak=7 + new corr=2 >= 3",
        },
        {
            "pj_slug": "sys-bots",
            "material_count": 4,
            "weak_unprocessed": 4,
            "new_corrections": 0,
            "last_evolve_at": None,
            "activity_since": {"subagents": 12, "sessions": 3},
            "reason": "weak=4 (初回)",
        },
    ],
}

EMPTY_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
}


def _write_queue(data_dir: Path, payload: dict) -> None:
    (data_dir / "evolve-queue.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_env(tmp_path, monkeypatch):
    """install レイアウト env をでっち上げ DATA_DIR を tmp に固定する。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    # rl_common.resolve_data_dir は env をそのまま返す（marker 無し）。
    return source


def test_deliver_fires_with_waiting_queue(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, SAMPLE_QUEUE)
    restore_state._deliver_evolve_queue_notice()
    out = capsys.readouterr().out
    assert out  # 非空
    payload = json.loads(out.strip())
    assert "systemMessage" in payload
    assert "figma-to-code" in payload["systemMessage"]
    assert "sys-bots" in payload["systemMessage"]


def test_deliver_silent_on_empty_queue(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, EMPTY_QUEUE)
    restore_state._deliver_evolve_queue_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_when_no_queue_file(tmp_path, monkeypatch, capsys):
    _install_env(tmp_path, monkeypatch)
    restore_state._deliver_evolve_queue_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    restore_state._deliver_evolve_queue_notice()
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_env(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    restore_state._deliver_evolve_queue_notice()
    assert capsys.readouterr().out == ""


def test_deliver_does_not_write(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, SAMPLE_QUEUE)
    before = {p.name for p in source.iterdir()}
    restore_state._deliver_evolve_queue_notice()
    after = {p.name for p in source.iterdir()}
    assert before == after  # queue 通知は read-only


def test_handle_session_start_invokes_queue_notice(tmp_path, monkeypatch, capsys):
    """handle_session_start が queue 通知を配信フローに含む（配線回帰）。"""
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, SAMPLE_QUEUE)
    # checkpoint 無し環境（CLAUDE_PROJECT_DIR を tmp に向ける）
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "figma-to-code" in out
