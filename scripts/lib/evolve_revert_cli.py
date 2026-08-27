"""`bin/evolve-revert` CLI 本体ロジック（#402 PR-2 段階4 §6 / ADR-054 Phase D PR4 §2 `--list`）。

サブコマンドを持たない単一動作の CLI（``bin/evolve-tier`` はサブコマンド付きだが、revert
は「1 entry_id に対して1操作」なので分岐は不要）。``--list`` は entry_id 探索の前段導線
（読むだけ）なので唯一の例外として positional entry_id を省略できる。

使用例:
    bin/evolve-revert --list                            # 戻せる採用の一覧（read-only）
    bin/evolve-revert --list --json                      # 同上・JSON 出力
    bin/evolve-revert <entry_id>                       # 既定 dry-run（何が起きるか確認）
    bin/evolve-revert <entry_id> --apply                # 実際に戻す
    bin/evolve-revert <entry_id> --apply --allow-metadata-loss
    bin/evolve-revert <entry_id> --dump-before <path>   # revert せず before 本文を取り出す

CLI 自体は判定ロジックを持たない——段階3 の apply engine（``evolve_revert.apply_revert`` /
``evolve_revert.dump_before``）または一覧生成（``evolve_revert_listing.build_revert_listing``）
を呼ぶだけにする（設計正典 §6）。

exit code: 成功 0 / 失敗（entry not found・conflict・拒否等）1 / 引数エラー 2。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from evolve_revert import (
    ApplyResult,
    DumpResult,
    apply_revert,
    dump_before,
    find_entry,
    render_dry_run_header,
)
from evolve_revert_listing import build_revert_listing, render_revert_listing


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evolve-revert",
        description=(
            "採用した skill diff を1コマンドで戻す（#402 PR-2）。既定は dry-run。"
            "revert できるのは evolve drain 経由の採用のみ"
            "（optimize.py/run_loop.py 経由の採用は対象外・ADR-054 Phase D PR2/PR3 凍結中）。"
        ),
    )
    parser.add_argument(
        "entry_id", nargs="?", default=None,
        help="戻す対象の accept entry ID（戦果ボードの entry_id、または --list の出力から取得）",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_mode",
        help="戻せる採用の一覧を表示する（read-only・entry_id/--apply 等とは併用不可）",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="--list の出力を JSON にする（--list 以外では無効）",
    )
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

    if args.list_mode:
        if args.entry_id or args.apply or args.dump_before or args.allow_metadata_loss:
            parser.error("--list は entry_id / --apply / --dump-before 等と併用できません")
        return _run_list(args)

    if args.json:
        parser.error("--json は --list とのみ併用できます")

    if not args.entry_id:
        parser.error("entry_id を指定するか --list で一覧を確認してください")

    if args.dump_before and args.apply:
        print(
            "[evolve-revert] エラー: --dump-before と --apply は排他です（同時指定不可）",
            file=sys.stderr,
        )
        return 2

    if args.dump_before:
        return _run_dump_before(args)
    return _run_apply(args)


def _run_list(args: argparse.Namespace) -> int:
    items = build_revert_listing()
    if args.json:
        measured = bool(getattr(items, "measured", True))
        print(json.dumps(
            {
                "measured": measured,
                "reason": getattr(items, "reason", None),
                "dropped_lines": int(getattr(items, "dropped_lines", 0)),
                "total": len(items) if measured else None,
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    for line in render_revert_listing(items):
        print(line)
    return 0


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
    # #469: 既定 dry-run の出力が result.message の3行程度に留まり「何が起きるか」が
    # 分からなかった。対象パス（絶対 + repo 相対）と判定分岐を、apply engine が既に
    # 持っている情報（result.target_path/branch + entry の relative_path）から組み立て
    # てヘッダとして先頭に足す。新たな書込みは発生しない（read-only の再照会のみ）。
    if result.dry_run and result.target_path:
        relative_path = None
        lookup = find_entry(args.entry_id, result.slug)
        if lookup.entry is not None:
            relative_path = lookup.entry.get("relative_path")
        print(render_dry_run_header(
            target_path=result.target_path,
            relative_path=relative_path,
            branch=result.branch,
        ))
        print()
    print(result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
