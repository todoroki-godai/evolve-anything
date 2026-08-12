"""`bin/evolve-revert` CLI 本体ロジック（#402 PR-2 段階4 §6）。

サブコマンドを持たない単一動作の CLI（``bin/evolve-tier`` はサブコマンド付きだが、revert
は「1 entry_id に対して1操作」なので分岐は不要）。

使用例:
    bin/evolve-revert <entry_id>                       # 既定 dry-run（何が起きるか確認）
    bin/evolve-revert <entry_id> --apply                # 実際に戻す
    bin/evolve-revert <entry_id> --apply --allow-metadata-loss
    bin/evolve-revert <entry_id> --dump-before <path>   # revert せず before 本文を取り出す

CLI 自体は判定ロジックを持たない——段階3 の apply engine（``evolve_revert.apply_revert`` /
``evolve_revert.dump_before``）を呼ぶだけにする（設計正典 §6）。

exit code: 成功 0 / 失敗（entry not found・conflict・拒否等）1 / 引数エラー 2。
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from evolve_revert import ApplyResult, DumpResult, apply_revert, dump_before


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evolve-revert",
        description="採用した skill diff を1コマンドで戻す（#402 PR-2）。既定は dry-run。",
    )
    parser.add_argument("entry_id", help="戻す対象の accept entry ID（戦果ボードの entry_id）")
    parser.add_argument(
        "--apply", action="store_true",
        help="実際に revert を実行する（既定は dry-run・書込ゼロ）",
    )
    parser.add_argument(
        "--dump-before", metavar="PATH", default=None,
        help="revert を実行せず before 本文の全文を PATH へ取り出す（--apply とは排他）",
    )
    parser.add_argument(
        "--allow-metadata-loss", action="store_true",
        help="初回検査で既にあったメタデータ損失（所有者・xattr・flags）のみ override する",
    )

    args = parser.parse_args(argv)

    if args.dump_before and args.apply:
        print(
            "[evolve-revert] エラー: --dump-before と --apply は排他です（同時指定不可）",
            file=sys.stderr,
        )
        return 2

    if args.dump_before:
        return _run_dump_before(args)
    return _run_apply(args)


def _run_dump_before(args: argparse.Namespace) -> int:
    result: DumpResult = dump_before(args.entry_id, args.dump_before)
    if result.ok:
        print(f"[evolve-revert] 書き出しました: {result.path}")
        return 0
    print(f"[evolve-revert] 失敗しました（理由: {result.reason}）", file=sys.stderr)
    return 1


def _run_apply(args: argparse.Namespace) -> int:
    result: ApplyResult = apply_revert(
        args.entry_id,
        dry_run=not args.apply,
        allow_metadata_loss=args.allow_metadata_loss,
    )
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
