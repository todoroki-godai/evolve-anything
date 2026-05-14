"""rl-fleet CLI エントリポイント (argparse + status / discover / test-guard サブコマンド)。

`tokens` サブコマンドは `cli_tokens.py` に分離済み。fleet/__init__.py から
re-export される（後方互換、`bin/rl-fleet` は `fleet.main` を呼ぶ）。
"""
from __future__ import annotations

import argparse
import json as _json
from pathlib import Path

from . import (
    STATUS_STALE,
    _DEFAULT_MAX_WORKERS,
    _DEFAULT_TIMEOUT_SEC,
)
from .cli_tokens import _inject_token_metrics, _run_tokens
from .collectors import collect_fleet_status, write_fleet_run
from .formatters import format_status_table
from .project_loader import enumerate_projects


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
    import fleet_config
    import test_guard

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])
    if tracked:
        projects = [Path(p) for p in tracked]
    else:
        root = args.root or Path.home() / "tools"
        projects = enumerate_projects(root)

    rows = test_guard.collect_test_guard_rows(projects)
    if getattr(args, "json", False):
        print(_json.dumps([{
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
