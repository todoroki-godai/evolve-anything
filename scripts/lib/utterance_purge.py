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

判定の厳格化（PR #377 codex レビュー Must1・2巡目で疑問符ヒューリスティックを撤回し構造判定に置換）:
  - `is_machinery_prompt` は correction 検出等の除外用途では「先頭300文字以内に
    marker を含む」広い再現率が正しい設計だが、purge は破壊的操作のため
    「本文が機構出力そのもの」であることを追加要求する。`_is_confirmed_machinery`
    で marker/prefix が文頭（lstrip 直後オフセット0）にあることをまず要求した上で、
    型ごとに以下の**構造**を追加検証する（句読点ではなく形で判定する）:
      - tag 型 prefix（`<system-reminder>` 等の完結タグ + `<local-command` のように
        タグ名が動的なもの）: 開始タグから始まり、**対応する閉じタグで終わり、
        閉じタグの後に何も続かない**こと。閉じタグが本文に無ければ安全側で対象外。
      - bracket 型 prefix（`[request interrupted`）: 本文全体が `[...]` の
        ブレース構造そのもので終わること（閉じ `]` の後に何も続かない）。
      - marker 型（4件）: 文頭一致に加え、本文に**改行を含む**こと（機構ターンは
        必ず複数行のブロックであり、1行の人間の質問と構造で区別できる）。
    初回実装は「末尾6文字以内の疑問符」で除外する非対称なヒューリスティックだったが、
    codex 2巡目レビューで反例（marker 型は疑問符除外が未適用・prefix 型も疑問符が
    末尾6文字より前だと見逃す）が実測され、データ上の根拠も安全上の根拠もないため
    撤回した。本判定は**意図的に under-deletion 側に倒している**（取りこぼしは無害・
    誤削除は永久アーカイブの破壊）ため、単一行の正規機構ターン（改行を含まない実際の
    machinery 出力）は理論上取りこぼしうる。独自マーカー文字列は追加せず
    `rl_common.detection` の `MACHINERY_PREFIXES` / `MACHINERY_MARKERS`
    （public 昇格・PR #377 2巡目 Should）をそのまま再利用する。

削除の安全性（PR #377 codex レビュー Must2/Should3）:
  - Must2: 複数行の DELETE は `con.begin()`/`commit()`/`rollback()` で単一
    トランザクションに包む。途中で例外が起きても部分削除が残らない。
  - Should3: 検出（read_only 接続）と削除（write 接続）は別 connection・別
    snapshot のため、間に同一 PK の行が置換/削除されうる（TOCTOU）。削除直前に
    `text_hash` と `_is_confirmed_machinery` を再検証し、実際に削除できた行数を
    `deleted_count` として返す（候補数 `matched_count` とは独立して真の実績値）。

決定論・LLM 非依存・subprocess なし。

補足（2026-08-12 実測）: 起票時点（#369）は実 DB で 26 件混入していたが、その後 extractor 側の
再分類で該当行の `source_kind` が `dialogue` から `long_paste` 等へ変わり、本ツールのスキャン
対象（`source_kind='dialogue'`）から自然に外れた。現時点で実 DB に対し dry-run すると
matched_count=0 になるのはこのためで、本ツールの実装不備ではない（`is_machinery_prompt` 単体
でも0件と確認済み）。**本ツールは「今まさに26件消すもの」ではなく、同種の混入が再発した際に
効く検証・掃除ツールとして残す。**
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List

from rl_common.detection import (
    MACHINERY_MARKERS,
    MACHINERY_PREFIXES,
    is_machinery_prompt,
)
from utterance_archive import store as _store

try:
    import duckdb as _duckdb  # type: ignore

    HAS_DUCKDB = True
except ImportError:  # pragma: no cover
    HAS_DUCKDB = False

# 代表サンプルの既定表示件数。
DEFAULT_SAMPLE_SIZE = 5

_SELECT_DIALOGUE_SQL = """
SELECT source_path, line_no, pj_slug, session_id, timestamp, text, text_hash
FROM utterances
WHERE source_kind = 'dialogue'
"""

_SELECT_CURRENT_ROW_SQL = (
    "SELECT text, text_hash FROM utterances WHERE source_path = ? AND line_no = ?"
)

_DELETE_ROW_RETURNING_SQL = (
    "DELETE FROM utterances WHERE source_path = ? AND line_no = ? RETURNING source_path"
)

# tag型 prefix（開始タグから始まり `>` で閉じるもの。<local-command は動的タグ名なので
# ここには含めず個別に扱う）。
_CLOSED_TAG_PREFIXES = tuple(p for p in MACHINERY_PREFIXES if p.startswith("<") and p.endswith(">"))
# <local-command のようにタグ名が動的な prefix（末尾 `>` を持たない）。
_DYNAMIC_TAG_PREFIXES = tuple(p for p in MACHINERY_PREFIXES if p.startswith("<") and not p.endswith(">"))
# bracket型 prefix（`[request interrupted` 等。本文全体がブラケット構造で完結する）。
_BRACKET_PREFIXES = tuple(p for p in MACHINERY_PREFIXES if p.startswith("["))

# 開始タグのタグ名を抽出する正規表現（例: "<local-command-stdout>" → "local-command-stdout"）。
_TAG_NAME_RE = re.compile(r"^<([a-zA-Z][a-zA-Z0-9_-]*)>")


def _closed_tag_body(stripped: str) -> bool:
    """strip 済みテキストが `<tag>...</tag>` の形（開始タグから始まり、対応する閉じタグで
    終わり、閉じタグの後に何も続かない）かを判定する（codex 2巡目 Must1）。

    閉じタグの後に人間の文が続く（引用+質問/依頼）場合は False。対応する閉じタグが
    本文に存在しない場合も安全側で False（under-deletion）。
    """
    m = _TAG_NAME_RE.match(stripped)
    if not m:
        return False
    closing = f"</{m.group(1)}>"
    return stripped.lower().endswith(closing.lower())


def _is_confirmed_machinery(text: str) -> bool:
    """purge 専用の厳格判定（PR #377 Must1・2巡目で構造判定に置換）。

    `is_machinery_prompt` を必要条件としつつ、marker/prefix が文頭
    （lstrip 直後オフセット0）にあることに加え、型ごとの**構造**を追加要求する
    （句読点でなく形で判定。疑問符ヒューリスティックは非対称かつデータ上の根拠が
    なく撤回した）。意図的に under-deletion 側に倒しており、単一行の正規機構
    ターンを取りこぼしうる（誤削除より安全）。
    """
    if not is_machinery_prompt(text):
        return False
    stripped = text.strip()
    low = stripped.lower()

    if low.startswith(tuple(p.lower() for p in _CLOSED_TAG_PREFIXES)):
        return _closed_tag_body(stripped)

    if low.startswith(tuple(p.lower() for p in _DYNAMIC_TAG_PREFIXES)):
        return _closed_tag_body(stripped)

    if low.startswith(tuple(p.lower() for p in _BRACKET_PREFIXES)):
        # ブラケット構造そのもので完結（末尾が `]`）していること。閉じ括弧の後に
        # 人間の文が続く場合は対象外。
        return stripped.endswith("]")

    # marker型（4件）: 文頭一致に加え、改行を含む（複数行ブロック）ことを要求する。
    # 機構ターンは必ず複数行のブロックであり、1行の人間の質問と構造で区別できる。
    if low.startswith(tuple(m.lower() for m in MACHINERY_MARKERS)):
        return "\n" in stripped

    return False


def find_machinery_rows(db_path: Path) -> List[Dict[str, Any]]:
    """`source_kind='dialogue'` のうち `_is_confirmed_machinery` に該当する行を検出する。

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
    for source_path, line_no, pj_slug, session_id, timestamp, text, text_hash in rows:
        if _is_confirmed_machinery(text):
            matched.append(
                {
                    "source_path": source_path,
                    "line_no": line_no,
                    "pj_slug": pj_slug,
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "text": text,
                    "text_hash": text_hash,
                }
            )
    return matched


def _delete_confirmed_row(con: Any, row: Dict[str, Any]) -> bool:
    """1行を削除直前に再検証してから削除する（PR #377 Should3・TOCTOU 対策）。

    検出（read_only 接続）と削除（write 接続）は別 snapshot のため、間に同一 PK の
    行が置換/削除されうる。`text_hash` 一致と `_is_confirmed_machinery` を再確認し、
    実際に削除できた場合のみ True を返す。
    """
    current = con.execute(
        _SELECT_CURRENT_ROW_SQL, [row["source_path"], row["line_no"]]
    ).fetchone()
    if current is None:
        return False  # detection後に既に削除済み
    current_text, current_hash = current
    if current_hash != row["text_hash"]:
        return False  # detection後に内容が変わった行は削除しない
    if not _is_confirmed_machinery(current_text):
        return False
    deleted = con.execute(
        _DELETE_ROW_RETURNING_SQL, [row["source_path"], row["line_no"]]
    ).fetchall()
    return len(deleted) > 0


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

    deleted_count = 0
    with _store.connection(Path(db_path), repair=False) as con:
        if con is None:
            return result
        con.begin()
        try:
            for row in matched:
                if _delete_confirmed_row(con, row):
                    deleted_count += 1
        except Exception:
            con.rollback()
            raise
        con.commit()

    result["deleted_count"] = deleted_count
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
