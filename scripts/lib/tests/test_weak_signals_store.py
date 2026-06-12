"""weak_signals.store のテスト（#432）。

決定論・LLM 非依存。dry-run 書き込みゼロ（pitfall_dryrun_stateful_store_write）を
最下層 write まで貫通して assert する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from weak_signals.store import (  # noqa: E402
    WeakSignal,
    append_signals,
    compute_signal_key,
    existing_signal_keys,
    read_signals,
)


def _sig(channel="rephrase", **prov) -> WeakSignal:
    return WeakSignal(
        channel=channel,
        provenance={"detector": channel, **prov},
        detected_at="2026-06-10T00:00:00+00:00",
        session_id="sess-1",
        pj_slug="rl-anything",
    )


def test_signal_key_is_stable_for_same_provenance() -> None:
    """同じ channel + provenance なら signal_key は同一（dedup 安定キー）。"""
    a = compute_signal_key("rephrase", {"x": 1, "y": 2})
    b = compute_signal_key("rephrase", {"y": 2, "x": 1})  # key 順違い
    assert a == b
    c = compute_signal_key("rephrase", {"x": 1, "y": 3})
    assert a != c


def test_weak_signal_autofills_key() -> None:
    sig = _sig(line_no=5)
    assert sig.signal_key
    assert sig.signal_key == compute_signal_key(sig.channel, sig.provenance)


def test_append_then_read_roundtrip(tmp_path: Path) -> None:
    store = tmp_path / "weak_signals.jsonl"
    res = append_signals([_sig(line_no=1), _sig(channel="esc_interrupt", line_no=2)], path=store)
    assert res["written"] == 2
    assert res["dry_run"] is False
    recs = read_signals(store)
    assert len(recs) == 2
    assert {r["channel"] for r in recs} == {"rephrase", "esc_interrupt"}
    assert all(r["promoted"] is False for r in recs)


def test_weak_signal_defaults_expired_fields() -> None:
    """新規レコードは expired=False / expired_at=None で初期化される（#442 TTL）。"""
    sig = _sig(line_no=7)
    rec = sig.to_record()
    assert rec["expired"] is False
    assert rec["expired_at"] is None


def test_dedup_skips_existing_signal_key(tmp_path: Path) -> None:
    """同一 signal_key は再追記でスキップ（バッチ再実行の二重記録防止）。"""
    store = tmp_path / "weak_signals.jsonl"
    append_signals([_sig(line_no=1)], path=store)
    res = append_signals([_sig(line_no=1), _sig(line_no=2)], path=store)
    assert res["written"] == 1
    assert res["skipped_dup"] == 1
    assert len(read_signals(store)) == 2


def test_dedup_within_same_batch(tmp_path: Path) -> None:
    """同一バッチ内の重複も 1 件に畳む。"""
    store = tmp_path / "weak_signals.jsonl"
    res = append_signals([_sig(line_no=9), _sig(line_no=9)], path=store)
    assert res["written"] == 1
    assert res["skipped_dup"] == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """dry-run は store に一切書かない（最下層 write ゲート貫通）。"""
    store = tmp_path / "weak_signals.jsonl"
    res = append_signals([_sig(line_no=1), _sig(line_no=2)], path=store, dry_run=True)
    assert res["dry_run"] is True
    # 件数は「書くはずだった」を返すが…
    assert res["written"] == 2
    # …ファイルは作られない（書き込みゼロ）
    assert not store.exists()
    assert read_signals(store) == []


def test_dry_run_does_not_create_parent_dir(tmp_path: Path) -> None:
    """dry-run は親ディレクトリの作成すらしない。"""
    store = tmp_path / "nested" / "deeper" / "weak_signals.jsonl"
    append_signals([_sig(line_no=1)], path=store, dry_run=True)
    assert not store.parent.exists()


def test_existing_keys_empty_when_no_file(tmp_path: Path) -> None:
    assert existing_signal_keys(tmp_path / "nope.jsonl") == set()
