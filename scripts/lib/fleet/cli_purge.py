"""evolve-fleet purge-corrections サブコマンド本体（#206 運用対応）。

`auto_memory_purge.purge_mismatched_pending()`（#206 で新規追加された検出・除去
ロジック本体）への薄い CLI ラッパー。`cli.py` から分離する（`pr-start`/`pr-finish`
と同型・800行対策）。ロジック自体はここで再実装しない。
"""
from __future__ import annotations

import argparse
import json as _json
from pathlib import Path

import auto_memory_purge


def _run_purge(args: argparse.Namespace) -> int:
    """purge-corrections: 全 PJ の pending auto_memory_queue から project スコープ不一致
    correction を検出・除去する（既定 dry-run、`--apply` で実書込）。
    """
    if args.data_dir is not None:
        data_dir = args.data_dir
    else:
        import data_dir_migration

        data_dir = data_dir_migration.default_canonical()

    result = auto_memory_purge.purge_mismatched_pending(
        Path(data_dir), dry_run=not args.apply
    )

    if getattr(args, "json", False):
        print(_json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"[fleet:purge-corrections] data_dir: {data_dir}")
    print(f"[fleet:purge-corrections] scanned_slugs: {len(result['scanned_slugs'])}")
    if result["affected_slugs"]:
        print(f"[fleet:purge-corrections] affected_slugs: {', '.join(result['affected_slugs'])}")
    else:
        print("[fleet:purge-corrections] affected_slugs: なし（混入は検出されませんでした）")
    print(f"[fleet:purge-corrections] rejected_count: {result['rejected_count']}")
    print(f"[fleet:purge-corrections] removed_records: {result['removed_records']}")

    if result["dry_run"]:
        if result["affected_slugs"]:
            print(
                "[fleet:purge-corrections] dry-run（書込ゼロ）。"
                " 実際に除去するには `--apply` を付けて再実行してください。"
            )
        else:
            print("[fleet:purge-corrections] dry-run（書込ゼロ）。")
    else:
        print("[fleet:purge-corrections] --apply: キューファイルを書き換えました。")

    return 0
