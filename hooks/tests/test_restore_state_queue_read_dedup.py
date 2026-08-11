"""restore_state の evolve-queue.json 二重読み解消（#412 [Should]6）。

`_deliver_evolve_queue_notice` / `_build_session_proposal_output` / `_deliver_judge_cap_notice`
の3つが個別に `evolve-queue.json` を読んでいた（同じ内容を1セッション開始ごとに3回パース）。
`handle_session_start` は1回だけ読んで3箇所に使い回す。
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
from daily import queue_notice as _queue_notice  # noqa: E402


def _fresh_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _install_env(tmp_path, monkeypatch):
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return source


def _write_queue(data_dir: Path) -> None:
    payload = {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 0,
        "queue": [],
    }
    (data_dir / "evolve-queue.json").write_text(json.dumps(payload), encoding="utf-8")


def test_handle_session_start_reads_evolve_queue_once(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))

    calls = []
    orig = _queue_notice.read_queue

    def _counting_read_queue(data_dir):
        calls.append(data_dir)
        return orig(data_dir)

    monkeypatch.setattr(restore_state._queue_notice, "read_queue", _counting_read_queue)

    restore_state.handle_session_start({})
    capsys.readouterr()  # 出力内容は他テストの対象。ここでは呼び出し回数だけ見る。

    assert len(calls) == 1
