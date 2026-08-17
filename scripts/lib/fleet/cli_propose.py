"""evolve-fleet propose サブコマンド本体（#81 Phase 2）。

queue の待ち PJ に evolve --dry-run 提案をバッチ生成し、集約レポート（md+json）を作る。
`cli.py` から subcommand 本体を分離し 800 行ハード上限を守る
（`tokens` サブコマンドを `cli_tokens.py` に分離したのと同型）。
fleet/__init__.py から `_run_propose` として re-export される（後方互換）。

`--live` 時に使う `_gather_queue_result` は `cli.py` 側にあるため、循環 import を避けて
関数内で遅延 import する（`cli.py` は本モジュールをトップレベルで import するため）。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Optional

from . import _current_data_dir


def _queue_staleness_note(queue_data: dict) -> Optional[str]:
    """既定（非 --live）入力の evolve-queue.json が古い場合の advisory 1 行を返す。

    鮮度判定は `daily.freshness`（#351 で単一ソース化）を再利用する（再実装しない）。
    新しければ None（沈黙）。`generated_at` が判定不能（欠落・パース不能・tz なし・
    未来日時）なら「古くない」と決めつけず UNKNOWN として警告する — 判定不能を沈黙に
    倒すと producer 停止が恒久的に気づかれない（#351 と同じ失敗モード）。
    """
    from daily.freshness import (
        Freshness,
        age_in_hours,
        classify_freshness,
        format_elapsed,
    )
    from daily.queue_notice import DEFAULT_STALE_HOURS

    now = datetime.now(timezone.utc)
    generated_at = queue_data.get("generated_at", "")
    # 閾値は queue_notice.DEFAULT_STALE_HOURS を単一ソースにする（#490 codex [Must]:
    # ここだけ 72 時間判定のままだと、同じ evolve-queue.json が SessionStart では STALE、
    # fleet propose では FRESH という食い違いを起こす）。
    freshness, age_days = classify_freshness(
        generated_at,
        now,
        stale_hours=DEFAULT_STALE_HOURS,
    )
    if freshness == Freshness.STALE:
        elapsed = format_elapsed(age_in_hours(generated_at, now=now), age_days)
        return (
            f"[fleet:propose] ⚠ evolve-queue.json が {elapsed}に生成されています"
            f"（--live で最新化できます）。"
        )
    if freshness == Freshness.UNKNOWN:
        return (
            "[fleet:propose] ⚠ evolve-queue.json の生成時刻を判定できません"
            "（欠落・不正な形式・未来日時のいずれか）。鮮度は不明です"
            "（--live で最新化できます）。"
        )
    return None


def run_propose_command(args: argparse.Namespace) -> int:
    """propose サブコマンド: queue の待ち PJ に evolve --dry-run 提案をバッチ生成する（#81）。

    既定は `DATA_DIR/evolve-queue.json`（Phase 1b #80 の派生物）を読む。`--live` は
    `_gather_queue_result`（queue サブコマンドと同一ロジック）を直接実行して最新化する。
    """
    from .propose import (
        build_batch_report,
        confirm_batch,
        estimate_cost,
        format_cost_confirmation,
        render_cli_summary,
        run_propose_batch,
        select_targets,
        write_reports,
    )

    data_dir = _current_data_dir()

    if args.live:
        from .cli import _gather_queue_result

        queue_data = _gather_queue_result(args)
    else:
        from daily.queue_notice import read_queue

        queue_data = read_queue(data_dir)
        if queue_data is None:
            print(
                "[fleet:propose] evolve-queue.json が見つかりません。"
                " `--live` で最新化するか、先に `bin/evolve-daily-run`"
                f"（または `evolve-fleet queue --json > {data_dir / 'evolve-queue.json'}`)"
                " を実行してください。"
            )
            return 1
        note = _queue_staleness_note(queue_data)
        if note:
            print(note)

    targets = select_targets(queue_data, max_pj=args.max_pj)
    if not targets:
        print("[fleet:propose] queue に待ち PJ がありません（対象 0 件）。")
        return 0

    cost = estimate_cost(targets)
    print(format_cost_confirmation(cost))
    if not confirm_batch(yes=args.yes):
        print("[fleet:propose] キャンセルしました。")
        return 1

    generated_at = datetime.now(timezone.utc).isoformat()
    batch = run_propose_batch(targets)
    report = build_batch_report(batch, generated_at=generated_at, cost=cost)
    md_path, json_path = write_reports(report, data_dir=data_dir)
    print(render_cli_summary(report, md_path, json_path))
    return 0
