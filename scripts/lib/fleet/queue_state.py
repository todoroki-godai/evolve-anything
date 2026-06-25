"""fleet.queue_state — per-PJ last_evolve state（#79 Phase 1a）。

``fleet queue`（#79）は「前回 evolve 以降に蓄積した学習素材」で待ち PJ を判定する。
既存 ``evolve-state.json`` はグローバル（全 PJ 共通の最終 evolve 時刻）で PJ 別に
測れないため、PJ 別の ``last_evolve_at`` を新ストアで保持する。

ストア形式（append-only jsonl + last-append-wins fold）:
  この PJ の慣習（reward_ema.jsonl / subagent_traces.jsonl / correction_review_seen.jsonl）に
  揃え、``{pj_slug, last_evolve_at, ts}`` を append し reader で pj_slug 単位に
  last-append-wins で fold する。単一 JSON dict 上書きより append-only の方が:
  - store_write barrier（ADR-049 / #55）に素直に乗る（atomic append primitive を共有）
  - 並行 evolve の write 競合を read-modify-write なしで吸収できる
  読み手（fleet queue）は最新の ``last_evolve_at`` だけ参照する。

書込境界:
  evolve の apply 境界（``evolve --drain``）が完了した PJ の ts を ``persist_last_evolve``
  で記録する（reward_ema #64 / weak_signals #484 と同型の apply-boundary 書込）。
  dry-run はゼロ書込（pitfall_dryrun_stateful_store_write）。

DATA_DIR は rl_common パッケージ属性（mock.patch.object(rl_common, "DATA_DIR") の SoT）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ストアの basename（store_registry / store_write の単一キー）。
STORE_NAME = "evolve-queue-state.jsonl"


def _data_dir(data_dir: Optional[Path]) -> Path:
    if data_dir is not None:
        return data_dir
    import rl_common
    return rl_common.DATA_DIR


def read_last_evolve(*, data_dir: Optional[Path] = None) -> Dict[str, str]:
    """per-PJ の最終 evolve 時刻 ``{pj_slug: last_evolve_at}`` を読む（読み取りのみ）。

    append 順 = 時系列なので pj_slug 単位に last-append-wins で fold する。ファイル
    不在 → ``{}``（ファイルを作らない・書かない = dry-run 純度）。破損 1 行は skip。
    """
    store = _data_dir(data_dir) / STORE_NAME
    out: Dict[str, str] = {}
    if not store.exists():
        return out
    try:
        text = store.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        slug = rec.get("pj_slug")
        last = rec.get("last_evolve_at")
        if not slug or not last:
            continue
        # append 順に上書き = 最後に見たもの（時系列で新しい）を採用。
        out[str(slug)] = str(last)
    return out


def persist_last_evolve(
    pj_slug: str,
    *,
    ts: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """apply 境界専用の書込 — pj_slug の最終 evolve 時刻を 1 レコード追記する。

    ``ts`` 未指定なら現在 UTC。``dry_run=True`` は store に一切触れず件数だけ返す
    （pitfall_dryrun_stateful_store_write）。書込は store_write barrier（ADR-049）経由。

    Returns:
        {"written": int, "pj_slug": str, "last_evolve_at": str, "dry_run": bool}
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    if dry_run:
        return {"written": 0, "pj_slug": pj_slug, "last_evolve_at": ts, "dry_run": True}

    record = {"pj_slug": pj_slug, "last_evolve_at": ts, "ts": ts}
    import rl_common
    rl_common.store_write(STORE_NAME, record)
    return {"written": 1, "pj_slug": pj_slug, "last_evolve_at": ts, "dry_run": False}
