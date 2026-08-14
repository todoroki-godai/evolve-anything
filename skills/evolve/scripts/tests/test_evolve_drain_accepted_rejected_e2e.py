"""#444 (#450 codex cold review [Must]4): CLI から実 drain_pending を通す E2E テスト。

`test_evolve_drain_accepted_rejected_cli.py` は `evolve_decisions.drain_pending` を
mock しているため、「applied だが --accepted なしは deferred」「--accepted を渡すと
optimize_history に載る」という核心の契約が CLI → 実 drain の経路で固定されていなかった
（synthetic E2E はレビュー時点で scratchpad の一時スクリプトのみで repo 未コミット）。

本ファイルは `evolve_decisions.drain_pending` を mock せず、`evolve.main()` 経由で
実際に marker → ingest_decisions → optimize_history_store の書込までを通す。他の
apply 境界 persist（weak_signals / reward_ema / queue_state / subagent_traces /
last_run_timestamp）は無関係なので `test_evolve_drain_capture_wiring.py` と同型の
`_stub_drain_neighbors` で無害化する（drain_pending 自体は stub しない — ここがこのファイルの主眼）。

HOME / MARKER_ROOT / DATA_DIR 隔離はルート conftest（#457/#119）の autouse fixture が
行う。実 `~/.claude/` には一切触れない。
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
import evolve_decisions as ed  # noqa: E402
import optimize_history_store as ohs  # noqa: E402


_BEFORE = "# e2e-skill\n\nトリガー: foo\n\n旧手順。\n"
_AFTER = "# e2e-skill\n\nトリガー: foo bar\n\n改善された手順を踏む。\n"

_SLUG = "e2e444slug"


def _stub_drain_neighbors(monkeypatch):
    """drain_pending 以外の apply 境界 persist を無害化する（drain_pending 自体は本物のまま）。"""
    from weak_signals import batch as ws_batch
    from audit import reward_ema as re
    from fleet import queue_state as qs
    from subagent_traces import ingest as st_ingest
    from evolve import _state

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
    monkeypatch.setattr(_state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False})


@pytest.fixture
def emitted_pending(monkeypatch, tmp_path):
    """実 emit_decisions で marker に pending 1件をスナップショットし、その後 skill を適用する。

    slug は monkeypatch で固定し、CLI 呼び出し側（drain_pending の内部 resolve_slug）と
    このフィクスチャの emit 側で同じ値になるようにする（bind-fence 済みの
    ``evolve_decisions.resolve_slug`` を経由するので両者は自然に一致する）。
    """
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: _SLUG)

    skill_dir = tmp_path / "skills" / "e2e-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(_BEFORE, encoding="utf-8")

    result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "e2e-skill", "skill_path": str(skill_file), "pattern": "p"}
                ]
            }
        }
    }
    out = ed.emit_decisions(result, dry_run=True, slug=_SLUG)
    pid = out["pending"][0]["id"]

    skill_file.write_text(_AFTER, encoding="utf-8")  # Step 3 相当の適用
    return skill_file, pid


def test_drain_without_accepted_defers_and_writes_nothing(emitted_pending, monkeypatch, capsys):
    """applied だが --accepted を渡さない → marker は維持され、optimize_history には何も書かれない
    （deferred）。CLI → 実 drain_pending の経路で固定する（#450 [Must]4 の核心1）。"""
    skill_file, pid = emitted_pending
    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(
        sys, "argv", ["evolve.py", "--drain", "--project-dir", str(skill_file.parent.parent.parent)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["accepted"] == []
    assert pid in out["deferred"]
    assert ohs.load_history(_SLUG) == []  # optimize_history には何も書かれない
    marker = ed.read_pending_marker(_SLUG)
    assert marker is not None  # marker は維持される（クリアされない）
    assert [e["id"] for e in marker["pending"]] == [pid]


def test_drain_with_accepted_records_accept_in_optimize_history(emitted_pending, monkeypatch, capsys):
    """--accepted <id> を渡す → optimize_history に accept が載り marker がクリアされる。
    CLI → 実 drain_pending の経路で固定する（#450 [Must]4 の核心2）。"""
    skill_file, pid = emitted_pending
    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", str(skill_file.parent.parent.parent),
         "--accepted", pid],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["accepted"] == [pid]

    history = ohs.load_history(_SLUG)
    assert len(history) == 1
    assert history[0]["human_accepted"] is True

    assert ed.read_pending_marker(_SLUG) is None  # marker はクリアされる


def test_drain_with_rejected_records_reject_and_no_accept(emitted_pending, monkeypatch, capsys):
    """--rejected <id> <理由> を渡す → optimize_history に reject（human_accepted=False）が
    載り、accept は0件のまま。CLI → 実 drain_pending の経路で固定する。"""
    skill_file, pid = emitted_pending
    _stub_drain_neighbors(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", str(skill_file.parent.parent.parent),
         "--rejected", pid, "ドメイン不一致"],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["accepted"] == []
    assert out["rejected"] == [pid]

    history = ohs.load_history(_SLUG)
    assert len(history) == 1
    assert history[0]["human_accepted"] is False
    assert ed.read_pending_marker(_SLUG) is None
