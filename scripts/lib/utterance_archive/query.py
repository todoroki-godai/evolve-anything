"""utterance_archive.query — utterances.db の読み取り API（#430）。

契約（design doc「query API の契約」）:
- ``query_utterances(pj_slug, ...)`` — pj_slug は **必須**。全PJ共通 DATA_DIR
  単一ファイル pitfall の再発防止（read 側照合の強制）。
- 横断検索は別関数 ``query_utterances_all_projects()`` を明示的に呼ぶ。
- ``source_kind`` のデフォルトは ``('dialogue',)``。long_paste / excluded_pj を
  含めるには明示 opt-in。下流（#431 個人辞書）の分母汚染を API デフォルトで防ぐ。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import store as _store

_COLUMNS = [
    "source_path", "line_no", "pj_slug", "session_id", "timestamp",
    "text", "text_hash", "prev_action", "source_kind",
    "extractor_version", "ingested_at",
]


def _rows_to_dicts(rows: List[tuple]) -> List[Dict[str, Any]]:
    return [dict(zip(_COLUMNS, r)) for r in rows]


def _build_query(
    *,
    pj_slug: Optional[str],
    since: Optional[str],
    keyword: Optional[str],
    session_id: Optional[str],
    source_kinds: Sequence[str],
    limit: Optional[int],
) -> tuple[str, list]:
    cols = ", ".join(_COLUMNS)
    where: List[str] = []
    params: List[Any] = []
    if pj_slug is not None:
        where.append("pj_slug = ?")
        params.append(pj_slug)
    if since is not None:
        where.append("timestamp >= ?")
        params.append(since)
    if keyword:
        where.append("text LIKE ?")
        params.append(f"%{keyword}%")
    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    if source_kinds:
        placeholders = ", ".join("?" for _ in source_kinds)
        where.append(f"source_kind IN ({placeholders})")
        params.extend(source_kinds)
    sql = f"SELECT {cols} FROM utterances"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY session_id, timestamp, line_no"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def _resolve_db_path(db_path: Optional[Path]) -> Path:
    if db_path is not None:
        return Path(db_path)
    from .ingest import default_db_path

    return default_db_path()


def query_utterances(
    pj_slug: str,
    since: Optional[str] = None,
    source_kinds: Sequence[str] = ("dialogue",),
    keyword: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """指定 PJ の発話を引く。pj_slug は必須（read 側照合の強制）。

    Args:
        pj_slug:      ADR-031 準拠の slug（必須）
        since:        ISO8601。これ以降の timestamp のみ（None = 制限なし、古い発話も含む）
        source_kinds: デフォルト ('dialogue',)。long_paste/excluded_pj は明示 opt-in
        keyword:      text LIKE 部分一致（None = 無視）
        session_id:   特定 session に絞る
        limit:        上限件数
    """
    if not pj_slug:
        raise ValueError("query_utterances には pj_slug が必須です（全PJ横断は query_utterances_all_projects を使う）")
    db_path = _resolve_db_path(db_path)
    if not _store.HAS_DUCKDB or not db_path.exists():
        return []
    sql, params = _build_query(
        pj_slug=pj_slug, since=since, keyword=keyword,
        session_id=session_id, source_kinds=tuple(source_kinds), limit=limit,
    )
    with _store.connection(db_path, repair=False) as con:
        if con is None:
            return []
        rows = con.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)


def query_utterances_all_projects(
    since: Optional[str] = None,
    source_kinds: Sequence[str] = ("dialogue",),
    keyword: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """全 PJ 横断で発話を引く（fleet recall 用の明示関数）。

    pj_slug 照合をスキップする横断検索なので、必ずこの関数名で呼ぶ
    （query_utterances の pj_slug 必須契約を迂回しない）。
    """
    db_path = _resolve_db_path(db_path)
    if not _store.HAS_DUCKDB or not db_path.exists():
        return []
    sql, params = _build_query(
        pj_slug=None, since=since, keyword=keyword,
        session_id=None, source_kinds=tuple(source_kinds), limit=limit,
    )
    with _store.connection(db_path, repair=False) as con:
        if con is None:
            return []
        rows = con.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)
