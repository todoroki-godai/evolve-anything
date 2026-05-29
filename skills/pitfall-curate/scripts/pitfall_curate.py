#!/usr/bin/env python3
"""pitfall-curate CLI — 任意PJの pitfalls.md を育てる PJ非依存ツールの入口。

決定論コア（純粋関数）は同ディレクトリの core.py。本ファイルは I/O と
サブコマンド dispatch だけを担う薄いラッパ。

サブコマンド:
  dedup         類似 pitfall ペアを検出
  supersede     old を new に superseded マーク
  unclassified  未分類 pitfall を JSON で列挙（agent が分類判断する材料）
  classify-set  agent が判断した Transferability/Generality を書き戻す
  distill       配布版（Top-N）を生成
  sync          記録↔分類↔配布の3層 drift を検出（--check で未同期なら exit 1）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core
import pitfall_registry


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _atomic_write(path: str, content: str) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(p)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pitfall-curate — pitfalls.md を育てる")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dedup = sub.add_parser("dedup", help="類似 pitfall ペアを検出")
    p_dedup.add_argument("--pitfalls", required=True)
    # 日本語主体の pitfalls は CJK bigram jaccard で 0.1-0.2 帯に分布する。
    # 英単語主体なら 0.3-0.4 が適切。コーパスに応じて --threshold で調整する。
    p_dedup.add_argument("--threshold", type=float, default=0.12)
    p_dedup.add_argument("--json", action="store_true")

    p_sup = sub.add_parser("supersede", help="old を new に superseded マーク")
    p_sup.add_argument("--pitfalls", required=True)
    p_sup.add_argument("--old", required=True)
    p_sup.add_argument("--new", required=True)

    p_unc = sub.add_parser("unclassified", help="未分類 pitfall を JSON で列挙")
    p_unc.add_argument("--pitfalls", required=True)

    p_set = sub.add_parser("classify-set", help="Transferability/Generality を設定")
    p_set.add_argument("--pitfalls", required=True)
    p_set.add_argument("--title", required=True)
    p_set.add_argument("--transferability", required=True, choices=core.TRANSFERABILITY)
    p_set.add_argument("--generality", type=int, required=True)

    p_dist = sub.add_parser("distill", help="配布版（Top-N）を生成")
    p_dist.add_argument("--pitfalls", required=True)
    p_dist.add_argument("--out", required=True)
    p_dist.add_argument("--top", type=int, default=20)
    p_dist.add_argument("--mandatory-generality", type=int, default=4)

    p_seed = sub.add_parser("seed", help="正準フォーマットの空ひな型を生成")
    p_seed.add_argument("--out", required=True)
    p_seed.add_argument("--force", action="store_true", help="既存ファイルを上書き")

    p_norm = sub.add_parser("normalize", help="既存ファイルを正準フォーマットへ変換")
    p_norm.add_argument("--pitfalls", required=True)
    p_norm.add_argument("--out", help="出力先（省略時は stdout。--pitfalls と同じパスで in-place）")
    p_norm.add_argument(
        "--check",
        action="store_true",
        help="書き換えず lint のみ（ok=0 / drift=1 / danger=2）。diff を提示する",
    )

    p_en = sub.add_parser(
        "enable", help="pitfalls.md を管理対象に登録（以後 hook が自動 lint）"
    )
    p_en.add_argument("--pitfalls", required=True)
    p_en.add_argument(
        "--project-dir",
        help="登録先 PJ ルート（省略時は CLAUDE_PROJECT_DIR / カレント）",
    )

    p_dis = sub.add_parser("disable", help="pitfalls.md を管理対象から外す")
    p_dis.add_argument("--pitfalls", required=True)
    p_dis.add_argument("--project-dir", help="対象 PJ ルート（省略時は CLAUDE_PROJECT_DIR / カレント）")

    p_sync = sub.add_parser("sync", help="3層 drift を検出")
    p_sync.add_argument("--pitfalls", required=True)
    p_sync.add_argument("--dist", required=True)
    p_sync.add_argument("--top", type=int, default=20)
    p_sync.add_argument("--mandatory-generality", type=int, default=4)
    p_sync.add_argument("--check", action="store_true", help="未同期なら exit 1")

    args = parser.parse_args(argv)

    if args.cmd == "dedup":
        parsed = core.parse_pitfalls(_read(args.pitfalls))
        pairs = core.find_similar_pairs(parsed, args.threshold)
        if args.json:
            print(json.dumps(pairs, ensure_ascii=False, indent=2))
        else:
            if not pairs:
                print(f"類似ペアなし (threshold={args.threshold})")
            for p in pairs:
                print(f"  score={p['score']}  {p['a']}  ↔  {p['b']}")
            print(f"\n{len(pairs)} ペア。supersede 記録: "
                  f"pitfall_curate.py supersede --old <旧> --new <新>")
        return 0

    if args.cmd == "supersede":
        content = _read(args.pitfalls)
        updated = core.mark_superseded(content, args.old, args.new)
        if updated == content:
            print(f"変更なし（既に記録済み or 対象なし）: {args.old}")
        else:
            _atomic_write(args.pitfalls, updated)
            print(f"✓ {args.old} → Superseded by {args.new}")
        return 0

    if args.cmd == "unclassified":
        parsed = core.parse_pitfalls(_read(args.pitfalls))
        print(json.dumps(core.list_unclassified(parsed), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "classify-set":
        content = _read(args.pitfalls)
        updated = core.set_classification(
            content, args.title, args.transferability, args.generality
        )
        if updated == content:
            print(f"対象が見つかりません: {args.title}", file=sys.stderr)
            return 1
        _atomic_write(args.pitfalls, updated)
        print(f"✓ {args.title}: {args.transferability}/G{args.generality}")
        return 0

    if args.cmd == "distill":
        parsed = core.parse_pitfalls(_read(args.pitfalls))
        sel = core.select_distill(parsed, args.top, args.mandatory_generality)
        md = core.render_distribution(parsed, sel["selected"])
        _atomic_write(args.out, md)
        print(f"✓ {len(sel['selected'])} 件を {args.out} に生成 "
              f"(必須 {len(sel['mandatory'])} / 落選 {len(sel['dropped'])})")
        return 0

    if args.cmd == "seed":
        if Path(args.out).exists() and not args.force:
            print(f"既に存在します（--force で上書き）: {args.out}", file=sys.stderr)
            return 1
        _atomic_write(args.out, core.render_seed())
        print(f"✓ 正準ひな型を生成: {args.out}")
        return 0

    if args.cmd == "normalize":
        if args.check:
            res = core.check_normalized(_read(args.pitfalls))
            if res["state"] == "ok":
                print("✓ 正準フォーマットです")
                return 0
            if res["state"] == "danger":
                print(f"✗ {res['reason']}", file=sys.stderr)
                return 2
            sys.stdout.write(res["diff"])
            print(
                "\n⚠ 正準フォーマットと差分があります。"
                "`normalize --out <path>` で正準化できます（承認後）。",
                file=sys.stderr,
            )
            return 1
        try:
            normalized = core.normalize(_read(args.pitfalls))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        if args.out:
            _atomic_write(args.out, normalized)
            print(f"✓ 正準フォーマットへ変換: {args.out}")
        else:
            sys.stdout.write(normalized)
        return 0

    if args.cmd == "enable":
        project_dir = (
            args.project_dir
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd()
        )
        res = core.check_normalized(_read(args.pitfalls))
        if res["state"] == "danger":
            print(f"✗ 登録できません: {res['reason']}", file=sys.stderr)
            print(
                "  index/TOC ファイルは pitfalls エントリファイルではありません。"
                "category ファイルを指定してください。",
                file=sys.stderr,
            )
            return 2
        added = pitfall_registry.add_managed(project_dir, args.pitfalls)
        if added:
            print(f"✓ 管理対象に登録: {args.pitfalls}")
        else:
            print(f"✓ 既に管理対象です: {args.pitfalls}")
        if res["state"] == "drift":
            print(
                "⚠ 現在のフォーマットは正準形と差分があります。"
                "`normalize --pitfalls <path> --out <path>` で正準化を推奨（承認後）。"
            )
        else:
            print("  フォーマットは正準形です。以後 hook が自動で lint します。")
        return 0

    if args.cmd == "disable":
        project_dir = (
            args.project_dir
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd()
        )
        if pitfall_registry.remove_managed(project_dir, args.pitfalls):
            print(f"✓ 管理対象から外しました: {args.pitfalls}")
        else:
            print(f"管理対象ではありません: {args.pitfalls}")
        return 0

    if args.cmd == "sync":
        parsed = core.parse_pitfalls(_read(args.pitfalls))
        report = core.check_sync(
            parsed, _read(args.dist), args.top, args.mandatory_generality
        )
        if report["healthy"]:
            print("✓ 3層は同期しています")
        else:
            if report["unclassified"]:
                print(f"⚠ 未分類: {', '.join(report['unclassified'])}")
            if report["missing_mandatory"]:
                print(f"⚠ 配布版に必須漏れ: {', '.join(report['missing_mandatory'])}")
            if report["stale"]:
                print(f"⚠ 配布版に資格なし（降格漏れ）: {', '.join(report['stale'])}")
        if args.check and not report["healthy"]:
            return 1
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
