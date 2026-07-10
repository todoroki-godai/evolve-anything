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

    ``daily.queue_notice``（Phase 1b #80）の staleness 判定を再利用する（再実装しない）。
    新しければ None（沈黙）。
    """
    from daily.queue_notice import DEFAULT_STALE_DAYS, _stale_days

    age = _stale_days(queue_data.get("generated_at", ""), datetime.now(timezone.utc))
    if age is not None and age >= DEFAULT_STALE_DAYS:
        return (
            f"[fleet:propose] ⚠ evolve-queue.json が {age} 日前に生成されています"
            f"（--live で最新化できます）。"
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
