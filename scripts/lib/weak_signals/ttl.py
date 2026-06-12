"""weak_signals.ttl — weak_signals.jsonl の TTL 失効マーク（#442）。

weak_signals は無期限に滞留しうるため、corrections の constraint_decay / triage_ledger と
揃えた 45 日 TTL を導入する。``detected_at`` から ``TTL_DAYS`` 超かつ **未昇格・未expired**
のレコードを ``expired=True`` に **マーク**（削除しない）し、昇格候補（``read_unpromoted``
の ``exclude_expired=True``）から自然に外す。「古い修正候補は腐る」を意図した間引きで、
TTL がそのまま品質フィルタとして機能する（設計 doc §機能 #5）。

書き換えは ``promote._rewrite_promoted`` と同型の原子的 rename（read-modify-write）。
昇格済み（``promoted=True``）レコードは既に本流へ抜けているので expired にしない。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: ``dry_run=True`` なら store に
**一切触れない**（mtime も変えない）。マークするはずだった件数だけ返す。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from weak_signals.store import default_store_path, read_signals

# corrections の constraint_decay / triage_ledger.DEFAULT_TTL_DAYS と整合（単一値で揃える）。
TTL_DAYS = 45


def _parse_iso(value: Any) -> Optional[datetime]:
    """ISO8601 文字列を aware datetime に。失敗時 None。"""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_expirable(rec: Dict[str, Any], cutoff: datetime) -> bool:
    """未昇格・未expired かつ detected_at が cutoff より古いレコードか。"""
    if rec.get("promoted"):
        return False
    if rec.get("expired"):
        return False
    detected = _parse_iso(rec.get("detected_at"))
    if detected is None:
        return False
    return detected < cutoff


def _rewrite(weak_signals_path: Path, records: List[Dict[str, Any]]) -> None:
    """weak_signals.jsonl を原子的に書き直す（promote._rewrite_promoted と同型）。"""
    new_content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    weak_signals_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(weak_signals_path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, weak_signals_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def mark_expired(
    *,
    weak_signals_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """detected_at から TTL_DAYS 超かつ未昇格・未expired を expired=True にマークする。

    削除はしない。dry_run はマークせず「マークするはずだった件数」だけ返し store に
    一切触れない（mtime 不変）。常時 emit 用に store 無しでも安全に 0 件を返す。

    Returns:
        {"expired": int, "scanned": int, "dry_run": bool}
    """
    ws_path = weak_signals_path if weak_signals_path is not None else default_store_path()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=TTL_DAYS)

    if not ws_path.exists():
        return {"expired": 0, "scanned": 0, "dry_run": dry_run}

    recs = read_signals(ws_path)
    expirable = [r for r in recs if _is_expirable(r, cutoff)]

    if dry_run:
        # 最下層: dry-run は store に一切書かない（mtime も変えない）。件数だけ返す。
        return {"expired": len(expirable), "scanned": len(recs), "dry_run": True}

    if expirable:
        marked_at = now.isoformat()
        for r in expirable:
            r["expired"] = True
            r["expired_at"] = marked_at
        _rewrite(ws_path, recs)

    return {"expired": len(expirable), "scanned": len(recs), "dry_run": False}
