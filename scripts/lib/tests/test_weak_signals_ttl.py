"""weak_signals.ttl のテスト（#442 — corrections decay 45 日と整合する TTL）。

detected_at から TTL_DAYS 超かつ未昇格・未expired のレコードを expired=True に原子的 rewrite
する。**削除しない**。dry_run はマークせず「マークするはずだった件数」だけ返し store の
mtime を一切変えない（pitfall_dryrun_stateful_store_write）。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from weak_signals import ttl as ws_ttl  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


_NOW = datetime(2026, 6, 12, 0, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _seed(ws_path: Path):
    """3 件: 古い未昇格 / 新しい未昇格 / 古いが昇格済み。"""
    sigs = [
        WeakSignal("rephrase", {"line_no": 1}, _iso(50), "s1", "rl-anything"),  # 期限切れ対象
        WeakSignal("rephrase", {"line_no": 2}, _iso(10), "s1", "rl-anything"),  # 新しい
        WeakSignal("rephrase", {"line_no": 3}, _iso(60), "s1", "rl-anything"),  # 古いが昇格済み
    ]
    append_signals(sigs, path=ws_path)
    # 3 件目を昇格済みにする
    recs = [json.loads(line) for line in ws_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    recs[2]["promoted"] = True
    ws_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
    )
    return sigs


def test_ttl_days_matches_constraint_decay() -> None:
    """TTL_DAYS は corrections の constraint_decay / triage_ledger の 45 日と揃う。"""
    assert ws_ttl.TTL_DAYS == 45


def test_mark_expired_marks_old_unpromoted(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    _seed(ws)
    res = ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW)
    assert res["expired"] == 1  # 50 日前の未昇格 1 件のみ
    assert res["scanned"] == 3
    assert res["dry_run"] is False

    recs = [json.loads(line) for line in ws.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_line = {r["provenance"]["line_no"]: r for r in recs}
    assert by_line[1]["expired"] is True
    assert by_line[1].get("expired_at")  # マーク時刻が入る
    assert by_line[2].get("expired", False) is False  # 新しいレコードは据え置き
    # 昇格済みは expired にしない（既に本流へ抜けている）
    assert by_line[3].get("expired", False) is False


def test_mark_expired_does_not_delete(tmp_path: Path) -> None:
    """期限切れは削除せずマークのみ（行数不変）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed(ws)
    ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW)
    recs = [line for line in ws.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(recs) == 3


def test_mark_expired_idempotent(tmp_path: Path) -> None:
    """既に expired のレコードは再カウントしない。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed(ws)
    ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW)
    res2 = ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW)
    assert res2["expired"] == 0


def test_mark_expired_dry_run_writes_nothing(tmp_path: Path) -> None:
    """dry_run は件数だけ返し store の mtime を一切変えない（実 E2E 相当）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed(ws)
    before = ws.read_text(encoding="utf-8")
    before_mtime = ws.stat().st_mtime_ns

    res = ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW, dry_run=True)
    assert res["dry_run"] is True
    assert res["expired"] == 1  # マークするはずだった件数
    assert ws.read_text(encoding="utf-8") == before  # 内容不変
    assert ws.stat().st_mtime_ns == before_mtime  # mtime 不変


def test_mark_expired_missing_file(tmp_path: Path) -> None:
    """store が無ければ 0 件で安全に返す（ファイルを作らない）。"""
    ws = tmp_path / "weak_signals.jsonl"
    res = ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW)
    assert res == {"expired": 0, "scanned": 0, "dry_run": False}
    assert not ws.exists()


def _seed_multi_pj(ws_path: Path):
    """2 PJ のレコードを混在させる（cross-PJ write 防止テスト用）。"""
    sigs_a = [
        WeakSignal("rephrase", {"line_no": 10}, _iso(50), "s1", "rl-anything"),  # PJA 期限切れ対象
    ]
    sigs_b = [
        WeakSignal("rephrase", {"line_no": 20}, _iso(50), "s2", "other-pj"),   # PJB 期限切れ対象
    ]
    append_signals(sigs_a, path=ws_path)
    append_signals(sigs_b, path=ws_path)


def test_mark_expired_only_current_pj(tmp_path: Path) -> None:
    """pj_slug 指定時は当PJのレコードのみ expired=True にし、他PJレコードは変更しない (#495)。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_multi_pj(ws)

    res = ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW, pj_slug="rl-anything")
    assert res["expired"] == 1  # rl-anything の期限切れ1件のみ
    assert res["scanned"] == 2  # 全件スキャン

    recs = [json.loads(line) for line in ws.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_line = {r["provenance"]["line_no"]: r for r in recs}
    # rl-anything は期限切れマーク済み
    assert by_line[10]["expired"] is True
    # other-pj は変更されていない
    assert by_line[20].get("expired", False) is False


def test_mark_expired_no_pj_slug_marks_all(tmp_path: Path) -> None:
    """pj_slug 未指定（後方互換）は全件を対象にする。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_multi_pj(ws)

    res = ws_ttl.mark_expired(weak_signals_path=ws, now=_NOW)
    assert res["expired"] == 2  # 両PJとも期限切れ対象
