"""reject 抑制（#446）: 却下された pending を次回 emit で再提示しない。

`proposal_id = f(repo_id, relative_path, before_sha)` は「対象の現在世代」を指す
（`evolve_decision_ids.py`）。reject 後は before_sha が不変な限り次回 emit も同じ
`proposal_id` を生成するが、emit 側は reject history を一切参照しないため無条件に
再提示されていた（#446 本文）。

本モジュールは新規ストアを作らず、既存の `remediation.suppression_ledger`
（`remediation_suppression/<slug>.jsonl`、TTL 既定45日・store_registry 登録済み）を
薄い adapter 経由で流用する。`filter_suppressed()`（issue dict → issue dict）を
直接は呼ばず、`load_ledger()`/`dedup_key()`/`record_rejection()` の3プリミティブだけを
借りて pending entry の形のまま判定する（`filter_suppressed()` は issue↔pending の
対応復元コストが別途要るため — 設計 §3.1-b 参照）。

設計: docs/decisions/drafts/446-reject-resuppression-design.md
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple


def _issue_for(entry: Dict[str, Any]) -> Dict[str, Any]:
    """pending entry から dedup_key() 用の issue dict を組み立てる（副作用なし）。

    detail.target = entry["id"]（= proposal_id）がキーの一意性を担保する唯一の成分
    （dedup_key() は detail の "target" キーを採用する既存契約・
    suppression_ledger.py:90 の allowlist に含まれる）。

    file は entry 由来にせず lane 固定のリテラル文字列にする（codex round2 [Must]1:
    dedup_key() は type + file + detail を丸ごとハッシュするため、file が entry ごとに
    変わると同じ proposal_id でも dedup_key が変わり抑制が成立しなくなる）。
    """
    proposal_type = entry.get("proposal_type") or "unknown"
    issue_type = "advisory" if proposal_type == "advisory" else "evolve_diff"
    return {
        "type": issue_type,
        "file": "evolve_decisions",  # lane 固定リテラル。entry 由来の値を混ぜない。
        "detail": {"target": entry.get("id")},
    }


def filter_rejected(
    pending: List[Dict[str, Any]],
    *,
    slug: str,
    now: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """reject 抑制中の候補を pending から除外する（順序保存・fail-open）。

    fail-open の境界を3段に分離する:
      1. ledger 読み込み（load_ledger()）失敗 → 全件そのまま通す（lane 全体は落とさない）
      2. 個別候補のキー計算（_issue_for()/dedup_key()）失敗 → その候補だけ抑制しない
      3. 個別レコードの decided_at/ttl_days が不正値 → その候補だけ抑制しない

    どの失敗経路でも「過剰抑制（指摘が黙って消える）」より「抑制しない」側に倒す。

    Returns: (kept_pending, stats)
    """
    from remediation.suppression_ledger import (
        DAY_SECONDS,
        DEFAULT_TTL_DAYS,
        dedup_key,
        load_ledger,
    )

    stats: Dict[str, Any] = {
        "suppressed_total": 0,
        "suppressed": [],
        "ledger_read_error": None,
        "candidate_errors": [],
    }
    try:
        ledger = load_ledger(slug)
    except (OSError, UnicodeDecodeError) as e:
        stats["ledger_read_error"] = f"{type(e).__name__}: {e}"
        return list(pending), stats

    now = now if now is not None else time.time()
    kept: List[Dict[str, Any]] = []
    for entry in pending:
        try:
            issue = _issue_for(entry)
            record = ledger.get(dedup_key(issue))
        except (AttributeError, KeyError, TypeError) as e:
            stats["candidate_errors"].append(
                {"id": entry.get("id"), "error": f"{type(e).__name__}: {e}"}
            )
            kept.append(entry)
            continue
        if record is None:
            kept.append(entry)
            continue
        try:
            decided_at = float(record.get("decided_at", 0.0))
            ttl_days = int(record.get("ttl_days", DEFAULT_TTL_DAYS))
            suppressed = now <= decided_at + ttl_days * DAY_SECONDS
        except (TypeError, ValueError):
            suppressed = False
        if suppressed:
            stats["suppressed_total"] += 1
            stats["suppressed"].append({"id": entry.get("id"), "file": issue["file"]})
        else:
            kept.append(entry)
    return kept, stats


def record_pending_rejection(
    entry: Dict[str, Any],
    *,
    slug: str,
    now: Optional[float] = None,
) -> Optional[str]:
    """reject された pending entry を suppression ledger に記録する。

    例外を投げない契約（呼び出し側 `_ingest.py` の判断記録・キュー消化を絶対に止めない）。
    `_issue_for()` の呼び出し自体も try に含める（entry の形が想定外でも本関数全体は
    落ちない）。戻り値は失敗時のみエラーメッセージ文字列、成功時 None。
    """
    from remediation.suppression_ledger import DEFAULT_TTL_DAYS, record_rejection

    try:
        issue = _issue_for(entry)
        record_rejection(
            issue,
            slug=slug,
            now=now,
            ttl_days=DEFAULT_TTL_DAYS,
            persist=True,
        )
        return None
    except (
        OSError,
        UnicodeDecodeError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        return f"{type(e).__name__}: {e}"
