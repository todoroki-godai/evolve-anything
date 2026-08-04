"""utterances.db に残る機構ターン混入行を除去する限定 purge ツール（#369）。

背景: `hooks/correction_detect.py`（writer 側）は #335 で harness 注入判定を
`rl_common.detection.is_machinery_prompt` に単一ソース化したが、
`utterance_archive/extractor.py` は独立した `_HARNESS_MARKERS` リストのまま
追随しておらず、Stop hook の自己出力（「先送り表現を検出しました」等）が
`source_kind='dialogue'` として utterances.db に混入していた。#323 で
extractor.py 側も `is_machinery_prompt` を追加適用し新規取り込み分は解消したが、
`fleet ingest` は mtime ベースの増分判定のため、修正前に既に取り込み済みの
既存行は再抽出されず DB に残り続ける（実測: 26 件・5 PJ・#369）。

本ツールは utterances テーブルの `source_kind='dialogue'` 行のみを対象に
`is_machinery_prompt` で再判定し、該当行だけを削除する one-shot ツール。
判定は既存関数を再利用し、独自のマーカーリストは持たない（#369 の根因が
まさに extractor.py が独立リストで判定を分岐させたことだったため）。

設計（`auto_memory_purge.py` に倣う）:
  - dry-run 既定（apply=False）。対象件数・PJ 別内訳・代表サンプルのみ返す。
  - apply=True で初めて削除する。
  - 検出（read）は read_only 接続で開く（pitfall_duckdb_read_opens_readwrite:
    通常の write-capable connect は初回 open で write transaction を伴い DB を
    fold して byte が変わる。dry-run が実 DB の byte を変えてはならないため）。
  - 削除（write）は `utterance_archive.store.connection()` 経由。物理 PK
    (source_path, line_no) で 1 行ずつ DELETE する。

決定論・LLM 非依存・subprocess なし。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from rl_common.detection import is_machinery_prompt
from utterance_archive import store as _store

try:
    import duckdb as _duckdb  # type: ignore

    HAS_DUCKDB = True
except ImportError:  # pragma: no cover
    HAS_DUCKDB = False

# 代表サンプルの既定表示件数。
DEFAULT_SAMPLE_SIZE = 5

_SELECT_DIALOGUE_SQL = """
SELECT source_path, line_no, pj_slug, session_id, timestamp, text
FROM utterances
WHERE source_kind = 'dialogue'
"""

_DELETE_ROW_SQL = "DELETE FROM utterances WHERE source_path = ? AND line_no = ?"


def find_machinery_rows(db_path: Path) -> List[Dict[str, Any]]:
    """`source_kind='dialogue'` のうち `is_machinery_prompt` に該当する行を検出する。

    read_only 接続で開くため DB には一切書き込まない。DB 不在 / DuckDB 未インストール
    なら空リストを返す。
    """
    db_path = Path(db_path)
    if not HAS_DUCKDB or not db_path.exists():
        return []
    con = _duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(_SELECT_DIALOGUE_SQL).fetchall()
    finally:
        con.close()

    matched: List[Dict[str, Any]] = []
    for source_path, line_no, pj_slug, session_id, timestamp, text in rows:
        if is_machinery_prompt(text):
            matched.append(
                {
                    "source_path": source_path,
                    "line_no": line_no,
                    "pj_slug": pj_slug,
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "text": text,
                }
            )
    return matched


def purge_machinery_utterances(
    db_path: Path, apply: bool = False, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> Dict[str, Any]:
    """機構ターン混入行を検出し、`apply=True` のときのみ削除する。

    Args:
        db_path: utterances.db のパス。
        apply:   False（既定）なら dry-run（検出のみ、書込ゼロ）。True なら実削除。
        sample_size: レポートに含める代表サンプルの上限件数。

    Returns:
        {
          "dry_run": bool,
          "matched_count": int,
          "by_pj": {pj_slug: count, ...},
          "sample": [{"pj_slug", "session_id", "timestamp", "text"}, ...],
          "deleted_count": int,
        }
    """
    matched = find_machinery_rows(db_path)

    by_pj: Dict[str, int] = {}
    for row in matched:
        by_pj[row["pj_slug"]] = by_pj.get(row["pj_slug"], 0) + 1

    sample = [
        {
            "pj_slug": row["pj_slug"],
            "session_id": row["session_id"],
            "timestamp": row["timestamp"],
            "text": row["text"],
        }
        for row in matched[:sample_size]
    ]

    result: Dict[str, Any] = {
        "dry_run": not apply,
        "matched_count": len(matched),
        "by_pj": by_pj,
        "sample": sample,
        "deleted_count": 0,
    }

    if not apply or not matched:
        return result

    with _store.connection(Path(db_path), repair=False) as con:
        if con is None:
            return result
        for row in matched:
            con.execute(_DELETE_ROW_SQL, [row["source_path"], row["line_no"]])
        result["deleted_count"] = len(matched)

    return result


def _format_report(report: Dict[str, Any], db_path: Path) -> str:
    lines = [
        f"[utterance-purge] target: {db_path}",
        f"[utterance-purge] matched (source_kind=dialogue & is_machinery_prompt): "
        f"{report['matched_count']}",
    ]
    for slug, count in sorted(report["by_pj"].items()):
        lines.append(f"[utterance-purge]   {slug}: {count}")
    for s in report["sample"]:
        preview = s["text"][:80].replace("\n", "\\n")
        lines.append(f"[utterance-purge]   sample ({s['pj_slug']}, {s['timestamp']}): {preview!r}")
    if report["dry_run"]:
        if report["matched_count"]:
            lines.append(
                "[utterance-purge] DRY-RUN（書込ゼロ）。実際に削除するには "
                "--apply を付けて再実行してください。"
            )
        else:
            lines.append("[utterance-purge] DRY-RUN（書込ゼロ）。対象なし。")
    else:
        lines.append(f"[utterance-purge] APPLIED. deleted_count={report['deleted_count']}")
    return "\n".join(lines)


def _default_db_path() -> Path:
    from utterance_archive.ingest import default_db_path

    return default_db_path()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "utterances.db の source_kind='dialogue' から機構ターン混入行"
            "（is_machinery_prompt 該当）を検出・除去する（#369、既定 dry-run）。"
        )
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="utterances.db のパス（既定: ADR-042 resolver 経由の DATA_DIR/utterances.db）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際に削除する（既定は dry-run）",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"レポートに含める代表サンプル件数（既定 {DEFAULT_SAMPLE_SIZE}）",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path if args.db_path is not None else _default_db_path()
    report = purge_machinery_utterances(db_path, apply=args.apply, sample_size=args.sample_size)
    print(_format_report(report, db_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
