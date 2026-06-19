"""#484 CLI 配線テスト: `evolve --drain` が決定論 weak_signals を永続化する。

根因（#484）: 標準フローは `evolve --dry-run` 分析 → 対話適用。run_evolve 内の
run_batch(dry_run=True) は #491 契約で常にゼロ書き込みなので、決定論3チャネルが実 PJ で
一度も永続化されない。修正は apply 境界（drain・tool 文脈・非 dry-run）で
persist_weak_signals_drain を呼ぶこと。本テストは main() の --drain 分岐がそれを呼び、
返り値サマリに `weak_signals_persisted` を載せることを固定する。

HOME 隔離はこのディレクトリの conftest（#457）が autouse で行う。
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


def test_drain_branch_persists_weak_signals(monkeypatch, capsys):
    """main() の --drain 分岐は persist_weak_signals_drain を呼び結果を surface する。"""
    import evolve_decisions as ed
    from weak_signals import batch as ws_batch

    # drain_pending は固定サマリへ差し替え（pending ロジックは test_evolve_drain が網羅）
    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )

    calls = {}

    def _fake_persist(slug, **kw):
        calls["slug"] = slug
        return {"detected": {"manual_edit_after_ai": 2}, "total": 2, "written": 2,
                "skipped_dup": 0, "dry_run": False}

    monkeypatch.setattr(ws_batch, "persist_weak_signals_drain", _fake_persist)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    # drain サマリに weak_signals 永続化結果が同居する
    assert out["weak_signals_persisted"]["written"] == 2
    assert out["weak_signals_persisted"]["dry_run"] is False
    # persist は slug を解決して呼ばれている（apply 境界の永続化が配線済み）
    assert "slug" in calls


def test_drain_branch_swallows_weak_signals_error(monkeypatch, capsys):
    """weak_signals 永続化が失敗しても drain 本体は完走し error を surface する。"""
    import evolve_decisions as ed
    from weak_signals import batch as ws_batch

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )

    def _boom(slug, **kw):
        raise RuntimeError("store unwritable")

    monkeypatch.setattr(ws_batch, "persist_weak_signals_drain", _boom)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["weak_signals_persisted"]
    assert "store unwritable" in out["weak_signals_persisted"]["error"]
