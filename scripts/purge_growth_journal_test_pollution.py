#!/usr/bin/env python3
"""growth-journal.jsonl からテスト汚染エントリを除去する one-time スクリプト（#420）。

実環境の growth-journal.jsonl が test 実行による汚染（project が ``test_*`` /
``tmp*`` 始まり）で大半を占めるため、それらのエントリを除去する。

設計:
  - **デフォルト dry-run**: 削除対象件数と内訳（バケツ別）を表示するだけ。
  - ``--apply`` で実際に除去し、apply 時は backup（``.bak.<ts>``）を作る。
  - ``unknown`` / 空 project のエントリは purge 対象外（別バケツとして件数報告のみ）。

純粋関数 ``classify_project`` / ``partition_records`` に判定を寄せ、I/O は
``purge_journal`` に集約する。LLM 非依存・決定論。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# purge 対象 project の prefix（test 実行由来）。
_TEST_PREFIXES = ("test_", "test-", "tmp")

# 別バケツ（purge 対象外）として件数報告のみ行う project 値。
_UNKNOWN_VALUES = ("", "unknown")

# バケツ名（report で使う）。
BUCKET_PURGE = "purge"
BUCKET_UNKNOWN = "unknown"
BUCKET_KEEP = "keep"


def classify_project(project) -> str:
    """project 値を 3 バケツ（purge / unknown / keep）に分類する純粋関数。

    - ``test_*`` / ``test-*`` / ``tmp*`` 始まり → purge
    - 空 / ``unknown`` → unknown（対象外・件数報告のみ）
    - それ以外 → keep
    """
    if project is None:
        return BUCKET_UNKNOWN
    name = str(project).strip()
    if name in _UNKNOWN_VALUES:
        return BUCKET_UNKNOWN
    if name.startswith(_TEST_PREFIXES):
        return BUCKET_PURGE
    return BUCKET_KEEP


def partition_records(
    records: List[dict],
) -> Tuple[List[dict], Dict[str, List[dict]]]:
    """records を「保持する行」と「バケツ別の分類」に分割する純粋関数。

    Returns:
        (kept, buckets):
          kept   = purge 対象を除いた残す行（unknown / keep を含む）
          buckets = {"purge": [...], "unknown": [...], "keep": [...]}
    """
    buckets: Dict[str, List[dict]] = {
        BUCKET_PURGE: [],
        BUCKET_UNKNOWN: [],
        BUCKET_KEEP: [],
    }
    kept: List[dict] = []
    for rec in records:
        # crystallization 以外の行は project を持たないので keep 側に倒す（安全側）。
        project = rec.get("project") if isinstance(rec, dict) else None
        bucket = classify_project(project)
        buckets[bucket].append(rec)
        if bucket != BUCKET_PURGE:
            kept.append(rec)
    return kept, buckets


def _read_records(journal_path: Path) -> List[dict]:
    """JSONL を読み込む（壊れた行はスキップ）。"""
    records: List[dict] = []
    with open(journal_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _backup_path(journal_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return journal_path.with_suffix(journal_path.suffix + f".bak.{ts}")


def purge_journal(journal_path: Path, apply: bool) -> dict:
    """growth-journal から test 汚染エントリを除去する。

    Args:
        journal_path: 対象 JSONL。
        apply: False（デフォルト）なら dry-run（件数のみ）。True なら実除去 + backup。

    Returns:
        report dict（バケツ別件数 / backup パス / applied フラグ）。
    """
    journal_path = Path(journal_path)
    if not journal_path.exists():
        return {
            "exists": False,
            "applied": False,
            "total": 0,
            "purge": 0,
            "unknown": 0,
            "keep": 0,
            "backup": None,
        }

    records = _read_records(journal_path)
    kept, buckets = partition_records(records)

    report = {
        "exists": True,
        "applied": False,
        "total": len(records),
        "purge": len(buckets[BUCKET_PURGE]),
        "unknown": len(buckets[BUCKET_UNKNOWN]),
        "keep": len(buckets[BUCKET_KEEP]),
        "backup": None,
    }

    if apply and buckets[BUCKET_PURGE]:
        backup = _backup_path(journal_path)
        shutil.copy2(journal_path, backup)
        with open(journal_path, "w", encoding="utf-8") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        report["applied"] = True
        report["backup"] = str(backup)

    return report


def _format_report(report: dict, journal_path: Path, apply: bool) -> str:
    if not report["exists"]:
        return f"[purge] {journal_path} not found — nothing to do."

    lines = [
        f"[purge] target: {journal_path}",
        f"[purge] total records       : {report['total']}",
        f"[purge] purge (test_/tmp*)  : {report['purge']}",
        f"[purge] unknown/empty (kept): {report['unknown']}",
        f"[purge] keep (real projects): {report['keep']}",
    ]
    if apply:
        if report["applied"]:
            lines.append(f"[purge] APPLIED. backup: {report['backup']}")
        else:
            lines.append("[purge] nothing to purge (no test entries).")
    else:
        lines.append("[purge] DRY-RUN (no changes). Re-run with --apply to remove.")
    return "\n".join(lines)


def _default_journal_path() -> Path:
    """実環境の growth-journal.jsonl パスを解決する。"""
    plugin_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(plugin_root / "scripts" / "lib"))
    try:
        import growth_journal  # noqa: WPS433

        return growth_journal._data_dir() / growth_journal.JOURNAL_FILENAME
    except Exception:
        return Path.home() / ".claude" / "evolve-anything" / "growth-journal.jsonl"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge test-pollution entries (project=test_*/tmp*) from growth-journal.jsonl"
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="growth-journal.jsonl path (default: resolved DATA_DIR)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually remove entries (default: dry-run). Creates a backup.",
    )
    args = parser.parse_args(argv)

    journal_path = args.path if args.path is not None else _default_journal_path()
    report = purge_journal(journal_path, apply=args.apply)
    print(_format_report(report, journal_path, args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
