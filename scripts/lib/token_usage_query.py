"""token_usage_query — TOP-N / WoW / cache hit 異常検出 / PJ ドリルダウン。

cache hit rate = cache_read / (cache_creation + cache_read)
(web_search/fetch は token sum に含めない、別軸)

期間境界は MAX(ts) を基準にしないと SoR の最新時刻と現在時刻のズレで取りこぼす。
ただし design ドキュメントは NOW() ベース。テストでは固定データを入れて NOW を前提に
動作確認するため、SQL は NOW() を使う。
"""
from __future__ import annotations

from typing import Any

try:
    from . import token_usage_store as _store  # type: ignore
except ImportError:  # pragma: no cover
    import token_usage_store as _store  # type: ignore


def _safe_query(sql: str, params: list[Any] | None = None) -> list[tuple]:
    if not _store.HAS_DUCKDB:
        return []
    if not _store.USAGE_DB.exists():
        return []
    try:
        return _store.query(sql, params)
    except Exception:
        return []


def top_n_consumers(days: int = 30, n: int = 3) -> list[dict]:
    """直近 N 日間の TOP-N 消費 PJ。

    Returns:
        [{'pj_id', 'pj_slug', 'tokens', 'cache_hit_pct', 'sessions'}, ...]
    """
    sql = f"""
    SELECT
        pj_id,
        ANY_VALUE(pj_slug) AS pj_slug,
        SUM(input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS tokens,
        SUM(cache_creation_input_tokens) AS cache_creation,
        SUM(cache_read_input_tokens) AS cache_read,
        COUNT(DISTINCT session_id) AS sessions
    FROM token_usage
    WHERE ts >= NOW() - INTERVAL {int(days)} DAY
    GROUP BY pj_id
    ORDER BY tokens DESC
    LIMIT {int(n)}
    """
    rows = _safe_query(sql)
    out = []
    for pj_id, pj_slug, tokens, cc, cr, sessions in rows:
        cc = cc or 0
        cr = cr or 0
        denom = cc + cr
        hit_pct = (cr * 100.0 / denom) if denom > 0 else None
        reuse_factor = (cr / cc) if cc > 0 else None
        out.append({
            "pj_id": pj_id,
            "pj_slug": pj_slug,
            "tokens": int(tokens or 0),
            "cache_hit_pct": hit_pct,
            "cache_reuse_factor": reuse_factor,
            "sessions": int(sessions or 0),
        })
    return out


def wow_anomalies(min_pct: float = 50.0, min_tokens: int = 1_000_000) -> list[dict]:
    """WoW (Week-over-Week) スパイク検出。14 日未満のデータは除外。"""
    sql = f"""
    WITH this_week AS (
        SELECT pj_id, ANY_VALUE(pj_slug) AS pj_slug,
               SUM(input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS tokens
        FROM token_usage
        WHERE ts >= NOW() - INTERVAL 7 DAY
        GROUP BY pj_id
    ),
    last_week AS (
        SELECT pj_id,
               SUM(input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS tokens
        FROM token_usage
        WHERE ts >= NOW() - INTERVAL 14 DAY
          AND ts <  NOW() - INTERVAL 7 DAY
        GROUP BY pj_id
    ),
    has_history AS (
        SELECT pj_id FROM token_usage
        GROUP BY pj_id
        HAVING MIN(ts) <= NOW() - INTERVAL 14 DAY
    )
    SELECT t.pj_id, t.pj_slug, t.tokens AS this_week, l.tokens AS last_week,
           (t.tokens - l.tokens) * 100.0 / NULLIF(l.tokens, 0) AS wow_pct
    FROM this_week t
    JOIN has_history h ON t.pj_id = h.pj_id
    LEFT JOIN last_week l ON t.pj_id = l.pj_id
    WHERE l.tokens IS NOT NULL
      AND ((t.tokens - l.tokens) * 100.0 / NULLIF(l.tokens, 0)) > {float(min_pct)}
      AND t.tokens > {int(min_tokens)}
    ORDER BY wow_pct DESC
    """
    rows = _safe_query(sql)
    return [
        {
            "pj_id": r[0],
            "pj_slug": r[1],
            "this_week": int(r[2] or 0),
            "last_week": int(r[3] or 0),
            "wow_pct": float(r[4] or 0.0),
        }
        for r in rows
    ]


def cache_hit_anomalies() -> list[dict]:
    """直近 7d hit < 40% かつ前 7d hit ≥ 60% で 20pt 以上低下した PJ。"""
    sql = """
    WITH this_week AS (
        SELECT pj_id, ANY_VALUE(pj_slug) AS pj_slug,
               SUM(cache_creation_input_tokens) AS cc,
               SUM(cache_read_input_tokens) AS cr
        FROM token_usage
        WHERE ts >= NOW() - INTERVAL 7 DAY
        GROUP BY pj_id
    ),
    last_week AS (
        SELECT pj_id,
               SUM(cache_creation_input_tokens) AS cc,
               SUM(cache_read_input_tokens) AS cr
        FROM token_usage
        WHERE ts >= NOW() - INTERVAL 14 DAY
          AND ts <  NOW() - INTERVAL 7 DAY
        GROUP BY pj_id
    )
    SELECT t.pj_id, t.pj_slug,
           (t.cr * 100.0 / NULLIF(t.cc + t.cr, 0)) AS this_hit,
           (l.cr * 100.0 / NULLIF(l.cc + l.cr, 0)) AS last_hit
    FROM this_week t
    JOIN last_week l ON t.pj_id = l.pj_id
    WHERE (t.cc + t.cr) > 0 AND (l.cc + l.cr) > 0
    """
    rows = _safe_query(sql)
    out = []
    for pj_id, pj_slug, this_hit, last_hit in rows:
        if this_hit is None or last_hit is None:
            continue
        if this_hit < 40.0 and last_hit >= 60.0 and (last_hit - this_hit) >= 20.0:
            out.append({
                "pj_id": pj_id,
                "pj_slug": pj_slug,
                "this_hit_pct": float(this_hit),
                "last_hit_pct": float(last_hit),
                "drop_pt": float(last_hit - this_hit),
            })
    return out


def pj_breakdown(pj_id: str, by: str = "session", limit: int = 10) -> list[dict]:
    """PJ ドリルダウン: by ∈ {'session','model','week'}。"""
    if by == "session":
        sql = f"""
        SELECT session_id,
               SUM(input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS tokens,
               SUM(cache_creation_input_tokens) AS cc,
               SUM(cache_read_input_tokens) AS cr,
               MIN(ts) AS first_ts
        FROM token_usage
        WHERE pj_id = ?
        GROUP BY session_id
        ORDER BY tokens DESC
        LIMIT {int(limit)}
        """
        rows = _safe_query(sql, [pj_id])
        return [
            {
                "key": r[0],
                "tokens": int(r[1] or 0),
                "cache_hit_pct": (r[3] * 100.0 / (r[2] + r[3])) if (r[2] or 0) + (r[3] or 0) > 0 else None,
                "first_ts": str(r[4]) if r[4] else None,
            }
            for r in rows
        ]
    elif by == "model":
        sql = f"""
        SELECT model,
               SUM(input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS tokens,
               COUNT(*) AS msgs
        FROM token_usage
        WHERE pj_id = ? AND model IS NOT NULL AND model != ''
        GROUP BY model
        ORDER BY tokens DESC
        LIMIT {int(limit)}
        """
        rows = _safe_query(sql, [pj_id])
        return [{"key": r[0], "tokens": int(r[1] or 0), "messages": int(r[2] or 0)} for r in rows]
    elif by == "week":
        sql = f"""
        SELECT date_trunc('week', ts) AS wk,
               SUM(input_tokens + output_tokens + cache_creation_input_tokens + cache_read_input_tokens) AS tokens
        FROM token_usage
        WHERE pj_id = ?
        GROUP BY wk
        ORDER BY wk DESC
        LIMIT {int(limit)}
        """
        rows = _safe_query(sql, [pj_id])
        return [{"key": str(r[0]), "tokens": int(r[1] or 0)} for r in rows]
    else:
        raise ValueError(f"unknown by={by!r}")
