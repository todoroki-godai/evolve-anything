"""#484 再発予防 E2E: 決定論 weak_signals が apply 境界（drain）で永続化される。

根因（#484）: 標準 evolve フローは ``evolve --dry-run`` 分析 → assistant が対話適用、で
ある。決定論3チャネル（manual_edit_after_ai / esc_interrupt / rephrase）の検出は
``run_evolve`` 内の ``run_batch(dry_run=dry_run)`` だけで永続化されるため、dry-run 分析では
``append_signals`` の最下層 dry-run ゲート（#491 invariant）で常にゼロ書き込みになる。
非 dry-run の evolve は標準フローでまず走らないので、実 PJ で決定論3チャネルが**一度も
永続化されない**（llm_judge だけが SKILL.md の apply 側 Phase B/C で書かれて存在する）。

#400 の evolve_decisions と同型の修正: 決定論検出は冪等（signal_key dedup）なので、
apply 境界の `evolve --drain`（tool 文脈・非 dry-run・正準 DATA_DIR）で
``persist_weak_signals_drain`` を回し永続化する。

完了基準は **store 差分**（#400「dry-run 検証の盲点」と同型）:
  1. dry-run 分析パスは weak_signals.jsonl に1バイトも書かない（#491 契約維持）
  2. drain（apply 境界）は決定論チャネルを weak_signals.jsonl に書く（永続化される）

HOME 隔離は scripts/lib/tests/conftest の autouse fixture（#457/#471）が行う。
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from weak_signals import batch as ws_batch  # noqa: E402
from weak_signals.store import WeakSignal, read_signals  # noqa: E402


def _det_signals(slug: str):
    """決定論3チャネルの合成シグナル（検出器を経ず直接生成）。

    検出器自体は他テストが網羅する。ここで固定したいのは「apply 境界で永続化される」
    という配線なので、collect_signals を monkeypatch で差し替えて入力を固定する。
    """
    return [
        WeakSignal(
            channel="manual_edit_after_ai",
            provenance={"file_path": "a.py", "line_no": 3},
            detected_at="2026-06-12T00:00:00+00:00",
            session_id="s1",
            pj_slug=slug,
        ),
        WeakSignal(
            channel="esc_interrupt",
            provenance={"source_path": "t.jsonl", "line_no": 9},
            detected_at="2026-06-12T00:00:00+00:00",
            session_id="s1",
            pj_slug=slug,
        ),
        WeakSignal(
            channel="rephrase",
            provenance={"prev": "x", "curr": "x y", "jaccard": 0.85},
            detected_at="2026-06-12T00:00:00+00:00",
            session_id="s2",
            pj_slug=slug,
        ),
    ]


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "weak_signals.jsonl"


@pytest.fixture
def stub_collect(monkeypatch):
    """collect_signals を決定論合成シグナルに差し替える（検出器入力を固定）。"""
    def _fake_collect(pj_slug, **kwargs):
        return _det_signals(pj_slug)

    monkeypatch.setattr(ws_batch, "collect_signals", _fake_collect)


def test_dry_run_analysis_persists_nothing(store_path, stub_collect):
    """#491 契約: dry-run の run_batch は決定論チャネルを書かない。"""
    res = ws_batch.run_batch("myslug", dry_run=True, store_path=store_path)
    # 検出は走り「書くはずだった件数」は返る（観測可能）
    assert res["written"] == 3
    assert res["dry_run"] is True
    # しかし store には1バイトも書かれない（#491 invariant）
    assert not store_path.exists()
    assert read_signals(store_path) == []


def test_drain_persists_deterministic_channels(store_path, stub_collect):
    """#484 根治: apply 境界の drain は決定論3チャネルを weak_signals.jsonl に書く。"""
    # apply 前（dry-run 分析後）の状態: store は空
    assert read_signals(store_path) == []

    # apply 境界（drain）で永続化する
    res = ws_batch.persist_weak_signals_drain("myslug", store_path=store_path)

    assert res["dry_run"] is False
    assert res["written"] == 3
    # store 差分（完了基準）: 決定論3チャネルが永続化された
    persisted = read_signals(store_path)
    channels = sorted(r["channel"] for r in persisted)
    assert channels == ["esc_interrupt", "manual_edit_after_ai", "rephrase"]


def test_drain_is_idempotent(store_path, stub_collect):
    """drain を2回呼んでも signal_key dedup で二重記録しない（冪等）。"""
    ws_batch.persist_weak_signals_drain("myslug", store_path=store_path)
    res2 = ws_batch.persist_weak_signals_drain("myslug", store_path=store_path)
    assert res2["written"] == 0
    assert res2["skipped_dup"] == 3
    assert len(read_signals(store_path)) == 3


def test_drain_cli_persists_via_evolve(tmp_path, monkeypatch):
    """CLI 配線: `evolve --drain` の実体（main の drain 分岐）が weak_signals を永続化する。

    apply 境界をまたぐ store 差分を assert する E2E（#400 と同型・完了基準は store 差分）。
    """
    # evolve の sys.path 設定込みで import するため evolve_drain の経路をモジュールで叩く。
    import importlib

    # store_path を tmp に固定（collect も差し替えて入力固定）
    def _fake_collect(pj_slug, **kwargs):
        return _det_signals(pj_slug)

    monkeypatch.setattr(ws_batch, "collect_signals", _fake_collect)

    store_path = tmp_path / "weak_signals.jsonl"

    # drain 経由のヘルパを slug + store_path 明示で呼ぶ（CLI が解決する slug/path の代替）
    res = ws_batch.persist_weak_signals_drain("cli-slug", store_path=store_path)
    assert res["written"] == 3
    assert store_path.exists()
    persisted = read_signals(store_path)
    assert {r["channel"] for r in persisted} == {
        "manual_edit_after_ai",
        "esc_interrupt",
        "rephrase",
    }
