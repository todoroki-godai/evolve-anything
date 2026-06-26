"""#64 CLI 配線テスト: `evolve --drain` が reward_ema バッチを永続化する。

MAA #64: 各スキルの advantage を evolve サイクル（バッチ）跨ぎで符号付き EMA 累積する。
書込は apply 境界（drain・tool 文脈・非 dry-run・正準 DATA_DIR）で行う
（weak_signals #484 と同型）。本テストは main() の --drain 分岐が
persist_reward_ema_batch を呼び、返り値サマリに `reward_ema_persisted` を載せること、
例外を握り潰すことを固定する。

HOME 隔離はこのディレクトリの conftest（#457）が autouse で行う。
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


def _stub_weak_signals(monkeypatch):
    """weak_signals 永続化は無関係なので固定値に差し替える。"""
    from weak_signals import batch as ws_batch
    monkeypatch.setattr(
        ws_batch, "persist_weak_signals_drain",
        lambda slug, **kw: {"written": 0, "dry_run": False},
    )


def test_drain_branch_persists_reward_ema(monkeypatch, capsys):
    """main() の --drain 分岐は persist_reward_ema_batch を呼び結果を surface する。"""
    import evolve_decisions as ed
    from audit import reward_ema as re

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )
    _stub_weak_signals(monkeypatch)

    calls = {}

    def _fake_persist(project_dir, **kw):
        calls["project_dir"] = project_dir
        calls["slug"] = kw.get("slug")
        return {"persisted": 2, "skills": ["a", "b"], "ts": "2026-06-10T00:00:00+00:00"}

    monkeypatch.setattr(re, "persist_reward_ema_batch", _fake_persist)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["reward_ema_persisted"]["persisted"] == 2
    # apply 境界の永続化が配線済み（project_dir を渡して呼ばれる）
    assert calls["project_dir"] == "/tmp/whatever"


def test_drain_without_project_dir_passes_non_none_to_reward_ema(monkeypatch, capsys):
    """`evolve --drain`（--project-dir 無し＝Step 7.8 の標準形）でも reward_ema に
    None でない project_dir が渡る（Path(None) クラッシュ根治・#64 drain 盲点）。

    `--project-dir` の argparse 既定は None。weak_signals / queue_state は
    `_resolve_pj_slug(None)` 経由で None を吸収するが、reward_ema は project_dir を
    直接 `Path()` に渡すため None だと TypeError で落ちていた（実 PJ drain で常時失敗）。
    """
    import evolve_decisions as ed
    from audit import reward_ema as re

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )
    _stub_weak_signals(monkeypatch)

    calls = {}

    def _fake_persist(project_dir, **kw):
        calls["project_dir"] = project_dir
        # 渡された project_dir が Path() に通せる（= None でない）ことを実地で固定する。
        Path(project_dir)
        return {"persisted": 0, "reason": "insufficient_skills"}

    monkeypatch.setattr(re, "persist_reward_ema_batch", _fake_persist)
    # --project-dir を付けない（標準の drain 起動形）。
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    # Path(None) で落ちず error キーにならない。
    assert "error" not in out["reward_ema_persisted"]
    assert calls["project_dir"] is not None


def test_drain_branch_swallows_reward_ema_error(monkeypatch, capsys):
    """reward_ema 永続化が失敗しても drain 本体は完走し error を surface する。"""
    import evolve_decisions as ed
    from audit import reward_ema as re

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )
    _stub_weak_signals(monkeypatch)

    def _boom(project_dir, **kw):
        raise RuntimeError("ema store unwritable")

    monkeypatch.setattr(re, "persist_reward_ema_batch", _boom)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["reward_ema_persisted"]
    assert "ema store unwritable" in out["reward_ema_persisted"]["error"]
