"""evolve-fleet tokens サブコマンド + token_usage SoR からのメトリクス注入。

`_inject_token_metrics` は status コマンドが FleetRow に tokens_30d / cache_hit_pct を
埋めるために使う。`_run_tokens` は `evolve-fleet tokens` サブコマンド本体。
fleet/__init__.py から re-export される（後方互換）。
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .collectors import FleetRow

from .formatters import _format_short_int


def _inject_token_metrics(rows: list[FleetRow], days: int = 30) -> None:
    """token_usage SoR から TOP-N 全体を引いて FleetRow に注入する。

    Match key: FleetRow.pj_name (basename) と consumer の pj_slug (pj_id から復元した
    basename・#68) を **basename 同士で完全一致**させる。データ無し PJ は None のまま。

    旧実装は両側を ``"-" 末尾 split`` で照合していた（``figma-to-code`` → ``code``）。
    これは pj_slug 化けバグ（#68）と同じ誤りを両側に持たせて偶然一致させていたため、
    top_n の slug を basename に修正した時点で row 側だけ旧 split のまま desync し、
    figma-to-code 等が "--" に、sys-bots が bots のトークンを誤って拾う回帰を生んだ。
    両側を basename に揃えて根治する。
    """
    try:
        import token_usage_query as tuq  # type: ignore
    except ImportError:
        return
    try:
        # 1 回で十分大きな N を取得して全 PJ をカバー
        consumers = tuq.top_n_consumers(days=days, n=10_000)
    except Exception:
        return
    if not consumers:
        return
    # pj_slug(basename) → metric。同名衝突時は最初を採用
    by_slug: dict[str, dict] = {}
    for c in consumers:
        slug = (c.get("pj_slug") or "").lower()
        if slug and slug not in by_slug:
            by_slug[slug] = c
    for row in rows:
        key = (row.pj_name or "").lower()  # FleetRow.pj_name は basename
        c = by_slug.get(key)
        if c is None:
            continue
        row.tokens_30d = c.get("tokens")
        hit = c.get("cache_hit_pct")
        row.cache_hit_pct = float(hit) if hit is not None else None
        reuse = c.get("cache_reuse_factor")
        row.cache_reuse_factor = float(reuse) if reuse is not None else None


def _resolve_pj_id(query: str) -> str | list[str] | None:
    """`--pj` 引数を DB 上の pj_id に解決する。

    - 完全一致 (pj_id or pj_slug) があればそれを返す
    - 部分一致 (pj_id endswith / contains) を試す
    - 候補 1 件 → str、複数 → list[str] (ambiguous)、ゼロ → None
    """
    try:
        import token_usage_store as tus  # type: ignore
    except ImportError:
        return None
    try:
        rows = tus.query(
            "SELECT pj_id, ANY_VALUE(pj_slug) FROM token_usage GROUP BY pj_id"
        )
    except Exception as e:
        print(f"[fleet tokens] _resolve_pj_id query failed: {e}", file=sys.stderr)
        return None
    pairs = [(r[0], r[1] or "") for r in rows]
    # 1. exact match (pj_id 優先 → 一意なら即返す)
    for pj_id, _slug in pairs:
        if query == pj_id:
            return pj_id
    # 1b. slug exact match (複数あり得るので集める)
    slug_hits = [pj_id for pj_id, slug in pairs if query == slug]
    if len(slug_hits) == 1:
        return slug_hits[0]
    if len(slug_hits) > 1:
        return sorted(slug_hits)
    # 2. endswith match (slug 形式の suffix)
    suffix = f"-{query}" if not query.startswith("-") else query
    endswith_hits = [pj_id for pj_id, _ in pairs if pj_id.endswith(suffix)]
    if len(endswith_hits) == 1:
        return endswith_hits[0]
    if len(endswith_hits) > 1:
        return sorted(endswith_hits)
    # 3. contains match (fallback)
    contains_hits = [pj_id for pj_id, _ in pairs if query in pj_id]
    if len(contains_hits) == 1:
        return contains_hits[0]
    if len(contains_hits) > 1:
        return sorted(contains_hits)
    return None


def _run_tokens(args) -> int:
    """`evolve-fleet tokens` サブコマンド。"""
    try:
        import token_usage_query as tuq  # type: ignore
        import token_usage_store as tus  # type: ignore
    except ImportError:
        print("token_usage modules not available", file=sys.stderr)
        return 1

    # backfill モード
    if getattr(args, "backfill", False):
        try:
            import token_usage_ingest as tui  # type: ignore
        except ImportError:
            print("token_usage_ingest not available", file=sys.stderr)
            return 1
        days = None if getattr(args, "all", False) else getattr(args, "days", 90)
        agg = tui.ingest_all_projects(days=days, progress=True)
        if getattr(args, "json", False):
            print(json.dumps(agg, ensure_ascii=False))
        else:
            print(
                f"[fleet tokens] backfill done: inserted={agg['inserted']} "
                f"skipped={agg['skipped']} files={agg['files_processed']} "
                f"projects={agg.get('projects', 0)}"
            )
        return 0

    days = getattr(args, "days", 30)

    # 空 DB チェック
    db_empty = (not tus.HAS_DUCKDB) or (not tus.USAGE_DB.exists())
    if not db_empty:
        try:
            row = tus.query("SELECT COUNT(*) FROM token_usage")
            db_empty = (not row) or (row[0][0] == 0)
        except Exception:
            db_empty = True

    if db_empty:
        msg = "[fleet tokens] No data. Run `evolve-fleet tokens --backfill` to ingest transcripts."
        print(msg, file=sys.stderr)
        if getattr(args, "json", False):
            print(json.dumps({"empty": True}, ensure_ascii=False))
        return 0

    # PJ 別ドリルダウン
    pj = getattr(args, "pj", None)
    if pj:
        by = getattr(args, "by", "session") or "session"
        resolved = _resolve_pj_id(pj)
        if resolved is None:
            print(f"[fleet tokens] pj not found: {pj!r}", file=sys.stderr)
            return 1
        if isinstance(resolved, list):
            print(
                f"[fleet tokens] ambiguous --pj {pj!r}, multiple matches:",
                file=sys.stderr,
            )
            for cand in resolved:
                print(f"  {cand}", file=sys.stderr)
            return 1
        rows = tuq.pj_breakdown(resolved, by=by, limit=10)
        if getattr(args, "json", False):
            print(json.dumps({"pj_id": resolved, "by": by, "rows": rows}, ensure_ascii=False, default=str))
        else:
            print(f"## {resolved} — breakdown by {by}")
            for r in rows:
                print(f"  {r['key']}\t{_format_short_int(r.get('tokens', 0))}")
        return 0

    # anomaly モード
    if getattr(args, "anomaly", False):
        wow = tuq.wow_anomalies()
        cache = tuq.cache_hit_anomalies()
        if getattr(args, "json", False):
            print(json.dumps({"wow": wow, "cache_hit": cache}, ensure_ascii=False, default=str))
        else:
            print("## Anomalies")
            for a in wow:
                print(f"  WoW: {a['pj_id']} +{a['wow_pct']:.0f}% ({_format_short_int(a['last_week'])} → {_format_short_int(a['this_week'])})")
            for a in cache:
                print(f"  cache: {a['pj_id']} {a['last_hit_pct']:.0f}% → {a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)")
        return 0

    # デフォルトサマリ: TOP 3 + anomaly
    top = tuq.top_n_consumers(days=days, n=3)
    wow = tuq.wow_anomalies()
    cache = tuq.cache_hit_anomalies()
    if getattr(args, "json", False):
        print(json.dumps({
            "top": top, "wow": wow, "cache_hit": cache, "days": days,
        }, ensure_ascii=False, default=str))
        return 0
    print(f"## Token Consumption (last {days} days)\n")
    print("TOP 3 consumers:")
    for i, c in enumerate(top, 1):
        hit = f" (cache hit {c['cache_hit_pct']:.0f}%)" if c.get("cache_hit_pct") is not None else ""
        print(f"  {i}. {c.get('pj_slug') or c['pj_id']}\t{_format_short_int(c['tokens'])}{hit}")
    if wow or cache:
        print("\nAnomalies detected:")
        for a in wow:
            print(f"  • {a['pj_id']}: WoW +{a['wow_pct']:.0f}% ({_format_short_int(a['last_week'])} → {_format_short_int(a['this_week'])})")
        for a in cache:
            print(f"  • {a['pj_id']}: cache hit {a['last_hit_pct']:.0f}% → {a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)")
    return 0
