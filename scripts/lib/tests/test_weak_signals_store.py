"""weak_signals.store のテスト（#432）。

決定論・LLM 非依存。dry-run 書き込みゼロ（pitfall_dryrun_stateful_store_write）を
最下層 write まで貫通して assert する。
"""
from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import shrink_freeze  # noqa: E402
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
        pj_slug="evolve-anything",
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


def test_missing_store_is_healthy_empty_with_read_health(tmp_path: Path) -> None:
    """#539: 未作成は読取失敗でなく、正常な空在庫として区別できる。"""
    store = tmp_path / "missing.jsonl"

    recs = read_signals(store)

    assert isinstance(recs, list)
    assert recs == []
    assert recs.read_health == {
        "sources": [
            {
                "path": str(store),
                "readable": True,
                "error": None,
                "malformed_lines": 0,
            }
        ]
    }


def test_healthy_empty_store_has_no_degradation(tmp_path: Path) -> None:
    """#539 陽性対照: 実在する空ファイルも健全な空在庫。"""
    store = tmp_path / "empty.jsonl"
    store.write_text("", encoding="utf-8")

    recs = read_signals(store)

    assert recs == []
    assert recs.read_health["sources"][0] == {
        "path": str(store),
        "readable": True,
        "error": None,
        "malformed_lines": 0,
    }


def test_permission_error_is_unreadable_not_healthy_empty(
    tmp_path: Path, monkeypatch
) -> None:
    """#539: OSError を空在庫へ丸めず source health に残す。"""
    store = tmp_path / "denied.jsonl"
    store.touch()
    real_open = builtins.open

    def _denied(path, *args, **kwargs):
        if Path(path) == store:
            raise PermissionError(13, "permission denied", store)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _denied)

    recs = read_signals(store)

    assert recs == []
    source = recs.read_health["sources"][0]
    assert source["path"] == str(store)
    assert source["readable"] is False
    assert "permission denied" in source["error"]
    assert source["malformed_lines"] == 0


def test_partial_corruption_keeps_valid_records_and_reports_bad_rows(tmp_path: Path) -> None:
    """#539: valid 行を返しつつ、破損行を無言 skip しない。"""
    store = tmp_path / "partial.jsonl"
    valid = _sig(line_no=11).to_record()
    store.write_text(
        json.dumps(valid, ensure_ascii=False) + "\n{not json\n[]\n",
        encoding="utf-8",
    )

    recs = read_signals(store)

    assert recs == [valid]
    assert recs.read_health["sources"][0] == {
        "path": str(store),
        "readable": True,
        "error": None,
        "malformed_lines": 2,
    }


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


# --- #379 Step 1 修正3: 凍結中の未知 channel 書込みゲート -----------------------

def test_append_signals_rejects_unknown_channel_when_frozen(tmp_path: Path, monkeypatch) -> None:
    """凍結中は正準集合（weak_signals.channels.WEAK_SIGNAL_CHANNELS）に無い channel の
    signal を書込み拒否する（未登録 channel が allowlist を経由せず書けていた穴の
    再発防止・外部レビュー指摘）。
    """
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", True)
    store = tmp_path / "weak_signals.jsonl"
    with pytest.raises(shrink_freeze.FreezeViolationError, match="#379"):
        append_signals([_sig(channel="brand_new_channel", line_no=1)], path=store)
    assert not store.exists()


def test_append_signals_rejects_unknown_channel_even_in_dry_run(
    tmp_path: Path, monkeypatch
) -> None:
    """dry-run でも channel 検証は書込み前に走る（file に触れないので dry-run 純度は破らない）。"""
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", True)
    store = tmp_path / "weak_signals.jsonl"
    with pytest.raises(shrink_freeze.FreezeViolationError):
        append_signals([_sig(channel="brand_new_channel", line_no=1)], path=store, dry_run=True)
    assert not store.exists()


def test_append_signals_allows_known_channel_when_frozen(tmp_path: Path, monkeypatch) -> None:
    """既存 6 channel は凍結中でも通常通り書込める（削除方向でなく既存維持は常に許容）。"""
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", True)
    store = tmp_path / "weak_signals.jsonl"
    res = append_signals([_sig(channel="rephrase", line_no=1)], path=store)
    assert res["written"] == 1


def test_append_signals_allows_unknown_channel_when_unfrozen(tmp_path: Path, monkeypatch) -> None:
    """凍結解除中（SHRINK_FREEZE_ACTIVE=False）は未知 channel も通す（将来の解除経路）。"""
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", False)
    store = tmp_path / "weak_signals.jsonl"
    res = append_signals([_sig(channel="brand_new_channel", line_no=1)], path=store)
    assert res["written"] == 1
