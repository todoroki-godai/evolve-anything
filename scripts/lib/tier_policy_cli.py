"""`bin/evolve-tier` CLI 本体ロジック（#193）。

サブコマンド:
- ``show [--json]``: ティア表 + 正典ソース（file/defaults）+ targets 件数
- ``init``: config 不在時に DEFAULT + 空 targets の雛形を書く（既存なら拒否）
- ``set TIER --model M [--effort E | --no-effort]``: 正典更新
- ``sync [--apply] [--json]``: 既定 dry-run（plan の diff 表示）。``--apply`` で書込
- ``drift [--json]``: stale-mention advisory の列挙

exit code: 正常 0 / 引数・バリデーションエラー 2 / config strict エラー 1。
"""
from __future__ import annotations

import argparse
import json as _json
import sys
from typing import List, Optional

import tier_policy
import tier_policy_drift
import tier_policy_sync

_STATUS_MARK = {"in_sync": "✓", "drift": "⚠", "skip": "・", "missing": "✗"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evolve-tier",
        description="モデルティア（HEAD/HARD/NORMAL/MECH/REVIEW）の正典を一元管理する CLI（#193）",
    )
    sub = parser.add_subparsers(dest="command")

    show_p = sub.add_parser("show", help="ティア表 + 正典ソースを表示（default）")
    show_p.add_argument("--json", action="store_true", help="JSON 出力")

    sub.add_parser("init", help="config 雛形を作成（既存なら拒否）")

    set_p = sub.add_parser("set", help="ティアの model/effort を更新")
    set_p.add_argument("tier", help="TIER 名（HEAD/HARD/NORMAL/MECH/REVIEW）")
    set_p.add_argument("--model", required=True, help="model エイリアス（opus/sonnet/haiku/fable）")
    effort_group = set_p.add_mutually_exclusive_group()
    effort_group.add_argument("--effort", default=None, help="effort（low/medium/high/xhigh/max）")
    effort_group.add_argument("--no-effort", action="store_true", help="effort を無しにする（haiku 等）")

    sync_p = sub.add_parser("sync", help="ティア正典を targets へ同期（既定 dry-run）")
    sync_p.add_argument("--apply", action="store_true", help="drift を実際に書き込む（既定は diff 表示のみ）")
    sync_p.add_argument("--json", action="store_true", help="JSON 出力")

    drift_p = sub.add_parser("drift", help="正典に無いモデルエイリアスの散文残存を検出")
    drift_p.add_argument("--json", action="store_true", help="JSON 出力")

    args = parser.parse_args(argv)

    if args.command == "init":
        return _run_init(args)
    if args.command == "set":
        return _run_set(args)
    if args.command == "sync":
        return _run_sync(args)
    if args.command == "drift":
        return _run_drift(args)

    # command 省略時も既定で show を実行する
    return _run_show(args)


def _run_show(args) -> int:
    config = tier_policy.load_tiers_config(strict=False)
    tiers = config.get("tiers") or {}
    source = config.get("_source", "file")
    targets = config.get("targets") or {}
    target_count = sum(len(v) for v in targets.values() if isinstance(v, list))

    if getattr(args, "json", False):
        print(_json.dumps({
            "tiers": tiers,
            "source": source,
            "target_count": target_count,
            "load_error": config.get("_load_error"),
        }, ensure_ascii=False, indent=2))
        return 0

    load_error = config.get("_load_error")
    suffix = f"（読込エラー: {load_error} — DEFAULT へ fail-open）" if load_error else ""
    print(f"[evolve-tier] 正典ソース: {source}{suffix}")
    for tier in tier_policy.TIER_ORDER:
        policy = tiers.get(tier, {})
        model = policy.get("model")
        effort = policy.get("effort")
        desc = policy.get("description", "")
        effort_str = effort if effort else "(非対応)"
        print(f"  {tier:6s} model={model!s:8s} effort={effort_str!s:8s} — {desc}")
    print(f"[evolve-tier] targets: {target_count} 件")
    return 0


def _run_init(args) -> int:
    path = tier_policy.tiers_config_path()
    try:
        tier_policy.init_config(config_path=path)
    except FileExistsError:
        print(f"[evolve-tier] 既に存在します: {path}（上書きしません）", file=sys.stderr)
        return 2
    print(f"[evolve-tier] 雛形を作成しました: {path}")
    return 0


def _run_set(args) -> int:
    effort = None if args.no_effort else args.effort
    try:
        result = tier_policy.set_tier(args.tier, args.model, effort)
    except ValueError as e:
        print(f"[evolve-tier] エラー: {e}", file=sys.stderr)
        return 2
    print(f"[evolve-tier] {result['tier']}: {result['old']} → {result['new']}")
    return 0


def _run_sync(args) -> int:
    try:
        config = tier_policy.load_tiers_config(strict=True)
    except ValueError as e:
        print(f"[evolve-tier] エラー: {e}", file=sys.stderr)
        return 1

    if args.apply:
        results = tier_policy_sync.apply_sync(config)
    else:
        results = tier_policy_sync.plan_sync(config)

    if getattr(args, "json", False):
        print(_json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    for r in results:
        mark = _STATUS_MARK.get(r["status"], "?")
        reason = f" — {r['reason']}" if r.get("reason") else ""
        print(f"{mark} [{r['status']}] {r['type']}: {r['path']}{reason}")
        if r.get("diff") and not args.apply:
            print(r["diff"])
    if not args.apply:
        drift_count = sum(1 for r in results if r["status"] == "drift")
        if drift_count:
            print(f"\n[evolve-tier] dry-run: {drift_count} 件が drift。--apply で書き込みます。")
    return 0


def _run_drift(args) -> int:
    try:
        config = tier_policy.load_tiers_config(strict=True)
    except ValueError as e:
        print(f"[evolve-tier] エラー: {e}", file=sys.stderr)
        return 1

    findings = tier_policy_drift.scan_stale_mentions(config)
    if getattr(args, "json", False):
        print(_json.dumps(findings, ensure_ascii=False, indent=2))
        return 0

    if not findings:
        print("[evolve-tier] stale mention なし")
        return 0
    for f in findings:
        print(f"{f['path']}:{f['line_no']}: {f['alias']!r} — {f['line'].strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
