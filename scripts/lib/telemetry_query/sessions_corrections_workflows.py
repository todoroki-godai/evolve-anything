"""sessions / corrections / workflows のクエリ層。

`_duckdb_query_file` (汎用 JSONL DuckDB ローダ) も Slice 3 で本ファイルに集約する。
`HAS_DUCKDB` / `DATA_DIR` は package (`__init__.py`) を SoT として
`from . import HAS_DUCKDB, DATA_DIR` 経由で参照する
（テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` 互換）。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .helpers import (
    _warn_no_duckdb,
    _load_jsonl,
    _filter_by_project,
    _filter_by_time,
    _build_time_where,
)


def query_sessions(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    sessions_file: Optional[Path] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """sessions をクエリして結果を返す。

    sessions_file 未指定:
        - `HAS_DUCKDB=True`: **SessionStore の union read** (`session_store.query`) を参照する。
          db の取り込み済み分 + 未 ingest（live）jsonl の両方が見える（#415 Phase A: append は
          jsonl 追記のみで ingest は非同期のため、db 直読では未 ingest セッションを取りこぼす。
          union read でこれを防ぐ）。
        - `HAS_DUCKDB=False`: 後方互換で `DATA_DIR / sessions.jsonl` を直接読む（DuckDB 非依存の
          フォールバック。union read は session_store の DuckDB 経路に依存するため不可）。
    sessions_file 指定:   後方互換のため指定された JSONL を読む（テスト用）。

    Args:
        project: フィルタするプロジェクト名。None の場合は全レコード。
        include_unknown: True の場合、project が null のレコードも含める。
        sessions_file: 明示的に JSONL パスを指定（後方互換、テスト用）。
        since: ISO 8601 文字列。この時刻以降のレコードのみ返す。
        until: ISO 8601 文字列。この時刻より前のレコードのみ返す。
    """
    from . import DATA_DIR, HAS_DUCKDB

    if sessions_file is not None:
        if not sessions_file.exists():
            return []
        if HAS_DUCKDB:
            return _duckdb_query_file(sessions_file, project=project, include_unknown=include_unknown, since=since, until=until)
        _warn_no_duckdb()
        records = _load_jsonl(sessions_file)
        records = _filter_by_project(records, project, include_unknown)
        return _filter_by_time(records, since, until)

    if not HAS_DUCKDB:
        # DuckDB 非依存フォールバック: DATA_DIR / sessions.jsonl を直読（union read は不可）。
        fallback = DATA_DIR / "sessions.jsonl"
        if not fallback.exists():
            return []
        _warn_no_duckdb()
        records = _load_jsonl(fallback)
        records = _filter_by_project(records, project, include_unknown)
        return _filter_by_time(records, since, until)

    # HAS_DUCKDB=True: SessionStore の union read（db + 未 ingest jsonl の dedup 合算）に揃える。
    return _query_sessions_via_store(project=project, include_unknown=include_unknown, since=since, until=until)


def _query_sessions_via_store(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """SessionStore の **cross-dir union read** を経由して sessions を取得しフィルタする。

    `read_session_records_union` は canonical + legacy/plugins-data の全候補 dir を
    (session_id, timestamp) で dedup 合算する（#45 ① read 統一・PR1 が outcome 系で導入した
    のと同一の cross-dir reader）。query_sessions はこれを使う**第2の session reader** だが、
    以前は `session_store.query()`（単一 DATA_DIR 内の db+jsonl union のみ）を呼んでおり
    cross-dir 未対応＝PR1 の partial fix 残りだった（pitfall_copied_parse_convention_partial_fix）。

    project フィルタは ``alias_aware=True``: PJ rename（rl-anything→evolve-anything）の legacy 行は
    旧 slug でタグ付けされたまま残るため、cross-dir union 単独では現 slug filter に弾かれ回収ゼロ
    になる（PR2 で usage/errors/subagents に入れた read 層 slug 別名と同じ罠）。canonical_pj_slug で
    両辺を畳んで legacy を当 PJ に回収する。union は全 PJ レコードを一度に返し Python で1回だけ
    フィルタするため二重カウントは起きない（usage/errors の accept_slug ループ方式とは別経路）。

    since は包含（>=）境界を維持するため reader へ push せず `_filter_by_time` で適用する
    （reader の since は排他 `>` で境界意味が異なる）。当 PJ の legacy は全件直近 90 日内のため
    since 窓では母集団が減らず（実測）、cold-path の union 全読みは ~0.5s で許容範囲。

    本番では telemetry_query と session_store は同一 DATA_DIR (`~/.claude/evolve-anything`) に解決し、
    pytest では conftest が両モジュールの DATA_DIR を同一 tmp に rebase する（HAS_DUCKDB=True 経路）。
    """
    import session_store

    records = session_store.read_session_records_union()
    records = _filter_by_project(records, project, include_unknown, alias_aware=True)
    return _filter_by_time(records, since, until)


def _duckdb_query_file(
    filepath: Path,
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
    timestamp_field: str = "timestamp",
) -> List[Dict[str, Any]]:
    """DuckDB で JSONL ファイルをクエリする。

    project カラムが存在しない既存データとの後方互換性を保つため、
    project フィルタ指定時はカラム存在チェックを行う。
    """
    import duckdb

    conn = duckdb.connect()
    try:
        read_expr = f"read_json_auto('{filepath}', ignore_errors=true)"
        params: Dict[str, Any] = {}
        where_parts: List[str] = []

        # project フィルタ
        if project is not None:
            cols_cursor = conn.execute(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {read_expr})")
            col_names = {row[0] for row in cols_cursor.fetchall()}

            if "project" not in col_names:
                if not include_unknown:
                    return []
                # include_unknown の場合は全レコード対象（project フィルタなし）
            else:
                params["project"] = project
                if include_unknown:
                    where_parts.append("(project = $project OR project IS NULL)")
                else:
                    where_parts.append("project = $project")

        # 時間範囲フィルタ
        time_clause = _build_time_where(since, until, params, timestamp_field=timestamp_field)
        if time_clause:
            where_parts.append(time_clause)

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        cursor = conn.execute(f"SELECT * FROM {read_expr}{where_sql}", params)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def query_corrections(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    corrections_file: Optional[Path] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """corrections.jsonl をクエリして結果を返す。

    corrections.jsonl は project_path（フルパス）を使用しており、
    project パラメータと照合するため末尾ディレクトリ名を抽出して比較する。
    """
    from . import DATA_DIR, HAS_DUCKDB

    filepath = corrections_file or (DATA_DIR / "corrections.jsonl")
    if not filepath.exists():
        return []

    if HAS_DUCKDB:
        return _duckdb_query_corrections(filepath, project=project, include_unknown=include_unknown, since=since, until=until)

    _warn_no_duckdb()
    records = _load_jsonl(filepath)
    records = _filter_corrections_by_project(records, project, include_unknown)
    return _filter_by_time(records, since, until)


def _filter_corrections_by_project(
    records: List[Dict[str, Any]],
    project: Optional[str],
    include_unknown: bool = False,
) -> List[Dict[str, Any]]:
    """corrections の project_path から末尾名を抽出してフィルタする。"""
    if project is None:
        return records
    result = []
    for rec in records:
        project_path = rec.get("project_path", "")
        if project_path:
            tail = project_path.rstrip("/").rsplit("/", 1)[-1] if "/" in project_path else project_path
            # projects ディレクトリパスの場合: -Users-xxx-project-name → project-name
            if tail.startswith("-"):
                tail = tail.rsplit("-", 1)[-1] if "-" in tail[1:] else tail
            if tail == project:
                result.append(rec)
                continue
        if include_unknown and not project_path:
            result.append(rec)
    return result


def _duckdb_query_corrections(
    filepath: Path,
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """DuckDB で corrections.jsonl をクエリする。project_path からの末尾名抽出でフィルタ。

    空ファイルや project_path カラム未存在の場合は project フィルタを安全にスキップする。
    """
    import duckdb

    conn = duckdb.connect()
    try:
        read_expr = f"read_json_auto('{filepath}', ignore_errors=true)"
        params: Dict[str, Any] = {}
        where_parts: List[str] = []

        # カラム存在チェック（空ファイル時は DuckDB が 'json' 単一カラムを返す）
        cols_cursor = conn.execute(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {read_expr})")
        col_names = {row[0] for row in cols_cursor.fetchall()}

        if project is not None:
            if "project_path" not in col_names:
                # project_path カラムが存在しない場合（空ファイル・旧フォーマット）
                if not include_unknown:
                    return []
                # include_unknown の場合は全レコード対象（フィルタなし）
            else:
                params["project"] = project
                if include_unknown:
                    where_parts.append(
                        "(string_split(project_path, '/')[-1] = $project OR project_path IS NULL)"
                    )
                else:
                    where_parts.append("string_split(project_path, '/')[-1] = $project")

        # timestamp カラムが存在する場合のみ時間フィルタを適用
        if "timestamp" in col_names:
            time_clause = _build_time_where(since, until, params)
            if time_clause:
                where_parts.append(time_clause)

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cursor = conn.execute(f"SELECT * FROM {read_expr}{where_sql}", params)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def query_workflows(
    *,
    workflows_file: Optional[Path] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """workflows.jsonl をクエリして結果を返す。

    Phase 1 では project フィルタなし（workflows.jsonl に project フィールドがないため）。
    時間範囲フィルタは started_at フィールドで適用する。
    """
    from . import DATA_DIR, HAS_DUCKDB

    filepath = workflows_file or (DATA_DIR / "workflows.jsonl")
    if not filepath.exists():
        return []

    if HAS_DUCKDB:
        return _duckdb_query_workflows(filepath, since=since, until=until)

    _warn_no_duckdb()
    records = _load_jsonl(filepath)
    return _filter_by_time(records, since, until, timestamp_field="started_at")


def _duckdb_query_workflows(
    filepath: Path,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """DuckDB で workflows.jsonl をクエリする。"""
    import duckdb

    conn = duckdb.connect()
    try:
        read_expr = f"read_json_auto('{filepath}', ignore_errors=true)"
        params: Dict[str, Any] = {}
        where_parts: List[str] = []

        if since:
            params["since"] = since
            where_parts.append("started_at >= $since")
        if until:
            params["until"] = until
            where_parts.append("started_at < $until")

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cursor = conn.execute(f"SELECT * FROM {read_expr}{where_sql}", params)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()
