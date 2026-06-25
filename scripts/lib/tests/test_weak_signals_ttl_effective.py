"""weak_signals.ttl.is_effectively_expired のテスト（#89）。

標準 evolve フロー（--dry-run 分析 → --drain 適用）は mark_expired を一度も通らず
expired フラグが書かれないため、read 側が detected_at から age を再計算しないと
45日超の腐った signal が material_count から永久に落ちない。is_effectively_expired は
detected_at + now の純関数で TTL を read 時に導出し、write 非依存にする。決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from weak_signals import ttl as ws_ttl  # noqa: E402


_NOW = datetime(2026, 6, 12, 0, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def test_effectively_expired_old_detected_at_no_flag() -> None:
    """detected_at が TTL_DAYS+1 日前・expired フラグ無しは expired 扱い（write 非依存）。"""
    rec = {"detected_at": _iso(ws_ttl.TTL_DAYS + 1)}
    assert ws_ttl.is_effectively_expired(rec, now=_NOW) is True


def test_effectively_expired_under_ttl_not_expired() -> None:
    """TTL_DAYS-1 日前（境界内）は expired ではない。"""
    rec = {"detected_at": _iso(ws_ttl.TTL_DAYS - 1)}
    assert ws_ttl.is_effectively_expired(rec, now=_NOW) is False


def test_effectively_expired_flag_true_short_circuits() -> None:
    """expired=True フラグ立ちは detected_at に関係なく expired（従来挙動）。"""
    rec = {"detected_at": _iso(1), "expired": True}
    assert ws_ttl.is_effectively_expired(rec, now=_NOW) is True


def test_effectively_expired_unparsable_detected_at_safe_side() -> None:
    """detected_at が parse 不能なら expired 扱いにしない（安全側＝False）。"""
    rec = {"detected_at": "not-a-date"}
    assert ws_ttl.is_effectively_expired(rec, now=_NOW) is False


def test_effectively_expired_missing_detected_at_safe_side() -> None:
    """detected_at 欠落（None）も expired 扱いにしない（安全側＝False）。"""
    rec = {"channel": "rephrase"}
    assert ws_ttl.is_effectively_expired(rec, now=_NOW) is False


def test_effectively_expired_uses_ttl_days_constant() -> None:
    """ちょうど TTL_DAYS 日前は cutoff（< 比較）ゆえ expired ではない（境界 == は残す）。"""
    rec = {"detected_at": _iso(ws_ttl.TTL_DAYS)}
    assert ws_ttl.is_effectively_expired(rec, now=_NOW) is False
