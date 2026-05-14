"""rl-anything fleet — 全 PJ 横断のメンテナンス拠点（Phase 1: status のみ）。

設計: `todoroki-main-design-20260422-140954.md` Phase 1 節。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

STATUS_ENABLED = "ENABLED"
STATUS_STALE = "STALE"
STATUS_NOT_ENABLED = "NOT_ENABLED"

AUDIT_OK = "OK"
AUDIT_TIMEOUT = "TIMEOUT"
AUDIT_ERROR = "ERROR"

from rl_common import DATA_DIR as _DEFAULT_DATA_DIR  # honors CLAUDE_PLUGIN_DATA

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_DEFAULT_AUTO_MEMORY_ROOT = Path.home() / ".claude" / "projects"
_DEFAULT_PROJECTS_ROOT = Path.home() / "tools"
_DEFAULT_RL_AUDIT_BIN = Path(__file__).resolve().parent.parent.parent / "bin" / "rl-audit"
_PLUGIN_KEY_PREFIX = "rl-anything@"
_SETTINGS_RETRY_SLEEP_SEC = 0.1
_DEFAULT_TIMEOUT_SEC = 10.0
_DEFAULT_MAX_WORKERS = 2
_KILL_GRACE_SEC = 2.0


def _current_data_dir() -> Path:
    """CLAUDE_PLUGIN_DATA を呼び出し時に再参照して DATA_DIR を返す。

    `rl_common._DEFAULT_DATA_DIR` は import-time capture のため env 後追い変更を
    反映できない。fleet-runs 書き出しなど呼び出しタイミングが重要な処理では
    こちらを使う。
    """
    env_val = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(env_val) if env_val else Path.home() / ".claude" / "rl-anything"


# project_loader (PJ 列挙 / 導入状況判定) は fleet/project_loader.py に集約済み（後方互換のため再エクスポート）
from .project_loader import (  # noqa: E402, F401
    _pj_safe_name,
    resolve_auto_memory_dir,
    enumerate_projects,
    _load_settings_with_retry,
    _is_plugin_enabled,
    _latest_activity,
    _safe_compute_level,
    classify_project,
)


# audit subprocess 実行 / 結果 dataclass は fleet/audit_runner.py に集約済み（後方互換のため再エクスポート）
from .audit_runner import (  # noqa: E402, F401
    AuditResult,
    IssuesSummary,
    _parse_iso,
    _parse_issues_summary,
    _terminate_process_group,
    run_audit_subprocess,
)


# Format helpers / status table は fleet/formatters.py に集約済み（後方互換のため再エクスポート）
from .formatters import (  # noqa: E402, F401
    _TABLE_HEADERS,
    _format_short_int,
    _format_cell_tokens,
    _format_cell_cache_hit,
    _format_relative,
    _format_cell_score,
    _format_cell_level,
    _format_cell_phase,
    _format_cell_last_audit,
    _format_cell_audit,
    _format_cell_issues,
    _format_cell_subagents,
    format_status_table,
)


# status 収集 / 永続化は fleet/collectors.py に集約済み（後方互換のため再エクスポート）
from .collectors import (  # noqa: E402, F401
    FleetRow,
    _collect_single,
    _find_duplicate_basenames,
    _serialize_row,
    aggregate_subagents_by_project,
    collect_fleet_status,
    write_fleet_run,
)


def main(argv: list[str] | None = None) -> int:
    """`bin/rl-fleet` エントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="rl-fleet",
        description="全 PJ 横断で rl-anything の健康状態を一覧表示する",
    )
    sub = parser.add_subparsers(dest="command")
    status_p = sub.add_parser("status", help="各 PJ のステータスを表形式で表示（default）")
    for p in (parser, status_p):
        p.add_argument("--root", type=Path, default=None, help="PJ 列挙のルート（config 未設定時の fallback、default: ~/tools）")
        p.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SEC, help="PJ 毎の audit タイムアウト秒 (default: 10)")
        p.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS, help="並列数 (default: 2)")
        p.add_argument("--no-write", action="store_true", help="fleet-runs/*.jsonl への追記をスキップ")
        p.add_argument("--all", dest="show_all", action="store_true", help="STALE/NOT_ENABLED PJ も含めて全表示（デフォルトは STALE 除外）")

    discover_p = sub.add_parser(
        "discover",
        help="Claude Code が認識している PJ を検出し、track/ignore を対話的に設定",
    )
    discover_p.add_argument("--non-interactive", action="store_true", help="候補を表示するのみ（承認なし）")

    tg_p = sub.add_parser(
        "test-guard",
        help="各 PJ で no-llm-in-tests / pytest-no-llm の導入状況を一覧",
    )
    tg_sub = tg_p.add_subparsers(dest="tg_command")
    tg_status = tg_sub.add_parser("status", help="導入状況を表形式で表示（default）")
    tg_status.add_argument("--root", type=Path, default=None,
                           help="PJ 列挙のルート (fleet-config 未設定時の fallback)")
    tg_status.add_argument("--json", action="store_true", help="JSON 出力")

    tokens_p = sub.add_parser(
        "tokens",
        help="PJ別 LLM トークン消費 (TOP-N / anomaly / drill-down / backfill)",
    )
    tokens_p.add_argument("--days", type=int, default=30, help="集計期間（日）。default: 30")
    tokens_p.add_argument("--pj", type=str, default=None, help="特定 PJ にドリルダウン (canonical pj_id)")
    tokens_p.add_argument("--by", type=str, default="session", choices=["session", "model", "week"], help="--pj 指定時の分解軸")
    tokens_p.add_argument("--anomaly", action="store_true", help="WoW + cache hit 異常のみ表示")
    tokens_p.add_argument("--backfill", action="store_true", help="transcript JSONL を ingest")
    tokens_p.add_argument("--all", action="store_true", help="--backfill 時に全期間 ingest（default 90 日）")
    tokens_p.add_argument("--json", action="store_true", help="JSON 出力")

    args = parser.parse_args(argv)

    if args.command == "discover":
        return _run_discover(args)
    if args.command == "tokens":
        return _run_tokens(args)
    if args.command == "test-guard":
        return _run_test_guard(args)

    # default: status
    return _run_status(args)


def _run_status(args) -> int:
    """fleet-config.json の tracked_projects を優先、未設定時は --root で fallback。"""
    import fleet_config

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])

    projects: list[Path] | None = None
    new_candidates: list[Path] = []
    if tracked:
        projects = [Path(p) for p in tracked]
        # ついでに新候補を検出して hint 表示
        discovered = fleet_config.filter_valid_projects(
            fleet_config.discover_cc_projects()
        )
        new_candidates = fleet_config.diff_candidates(config, discovered)

    rows = collect_fleet_status(
        root=args.root,
        timeout=args.timeout,
        max_workers=args.max_workers,
        projects=projects,
    )
    _inject_token_metrics(rows, days=30)
    show_all = getattr(args, "show_all", False)
    if not show_all:
        stale_count = sum(1 for r in rows if r.status == STATUS_STALE)
        rows = [r for r in rows if r.status != STATUS_STALE]
    else:
        stale_count = 0
    print(format_status_table(rows), end="")
    if not show_all and stale_count:
        print(f"[fleet] STALE {stale_count} PJ を非表示にしています（--all で全表示）")
    if new_candidates:
        print(
            f"\n[fleet] 新しい PJ 候補を {len(new_candidates)} 件検出しました。"
            f" `rl-fleet discover` で track/ignore を設定してください。",
        )
    if not args.no_write:
        write_fleet_run(rows)
    return 0


def _run_test_guard(args) -> int:
    """各 PJ の test-guard 導入状況を表示。tracked_projects 優先、未設定なら --root。"""
    import json
    import test_guard
    import fleet_config

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])
    if tracked:
        projects = [Path(p) for p in tracked]
    else:
        root = args.root or Path.home() / "tools"
        projects = enumerate_projects(root)

    rows = test_guard.collect_test_guard_rows(projects)
    if getattr(args, "json", False):
        print(json.dumps([{
            "pj_name": r.pj_name,
            "pj_path": str(r.pj_path),
            "languages": sorted(r.languages),
            "uses_llm": r.uses_llm,
            "has_precommit_hook": r.has_precommit_hook,
            "has_pytest_no_llm": r.has_pytest_no_llm,
            "needs_attention": r.needs_attention,
        } for r in rows], indent=2, ensure_ascii=False))
    else:
        print(test_guard.format_test_guard_table(rows), end="")
    return 0


def _run_discover(args) -> int:
    """CC の `~/.claude/projects/` から PJ を検出し、対話的に track/ignore を決定。"""
    import fleet_config

    config = fleet_config.load_config()
    discovered = fleet_config.filter_valid_projects(
        fleet_config.discover_cc_projects()
    )
    candidates = fleet_config.diff_candidates(config, discovered)

    tracked_count = len(config.get("tracked_projects", []))
    ignored_count = len(config.get("ignored_projects", []))
    print(f"[fleet] 既存設定: tracked={tracked_count}, ignored={ignored_count}")

    if not candidates:
        print("[fleet] 新しい PJ 候補はありません。")
        return 0

    print(f"[fleet] 新候補 {len(candidates)} 件:")
    for i, pj in enumerate(candidates, 1):
        markers = []
        if (pj / "CLAUDE.md").is_file():
            markers.append("CLAUDE.md")
        if (pj / ".claude").is_dir():
            markers.append(".claude/")
        marker_str = ", ".join(markers) if markers else "(none)"
        print(f"  [{i:>2}] {pj}  ({marker_str})")

    if args.non_interactive:
        print("[fleet] --non-interactive 指定のため承認せず終了。")
        return 0

    print()
    print("各 PJ について 'a' (track), 'i' (ignore), 's' (skip=次回再提案) で回答。")
    print("'q' で中断・保存。空入力は skip 扱い。")
    print()

    changed = False
    for i, pj in enumerate(candidates, 1):
        while True:
            ans = input(f"  [{i}/{len(candidates)}] {pj}: [a/i/s/q] ").strip().lower()
            if ans in ("a", "i", "s", "q", ""):
                break
            print("  → 'a' (track), 'i' (ignore), 's' (skip), 'q' (quit) のいずれかを入力")
        if ans == "q":
            print("[fleet] 中断しました。ここまでの変更を保存します。")
            break
        if ans == "a":
            fleet_config.track_project(config, pj)
            changed = True
            print(f"    → tracked")
        elif ans == "i":
            fleet_config.ignore_project(config, pj)
            changed = True
            print(f"    → ignored")
        # s or empty: skip (do nothing)

    if changed:
        from datetime import datetime, timezone
        config["last_discovery"] = datetime.now(timezone.utc).isoformat()
        fleet_config.save_config(config)
        final_tracked = len(config.get("tracked_projects", []))
        final_ignored = len(config.get("ignored_projects", []))
        print(f"\n[fleet] 保存しました: tracked={final_tracked}, ignored={final_ignored}")
    else:
        print("\n[fleet] 変更なし（skip のみ）。")
    return 0


def _inject_token_metrics(rows: list[FleetRow], days: int = 30) -> None:
    """token_usage SoR から TOP-N 全体を引いて FleetRow に注入する。

    Match key: FleetRow.pj_name (basename) と pj_slug (encoded path 末尾セグメント) を
    末尾一致で照合する。データ無し PJ は None のまま。
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
    # pj_slug → metric。同名衝突時は最初を採用
    by_slug: dict[str, dict] = {}
    for c in consumers:
        slug = (c.get("pj_slug") or "").lower()
        if slug and slug not in by_slug:
            by_slug[slug] = c
    for row in rows:
        # row.pj_name 末尾セグメントは "-" で区切られた最後 (例: "rl-anything" → "anything")
        # token_usage_ingest._pj_slug_from_id と同ロジック
        last = row.pj_name.rstrip("-").split("-")[-1].lower() if row.pj_name else ""
        c = by_slug.get(last)
        if c is None:
            continue
        row.tokens_30d = c.get("tokens")
        hit = c.get("cache_hit_pct")
        row.cache_hit_pct = float(hit) if hit is not None else None


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
    """`rl-fleet tokens` サブコマンド。"""
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
        msg = "[fleet tokens] No data. Run `rl-fleet tokens --backfill` to ingest transcripts."
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


if __name__ == "__main__":
    raise SystemExit(main())
