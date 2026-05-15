"""DuckDB ベースのテレメトリクエリ層。

JSONL ファイルを DuckDB の read_json_auto() で直接 SQL クエリする。
DuckDB 未インストール時は load_jsonl() + Python フィルタにフォールバック。

Phase 11 で `telemetry_query.py` を package 化。`HAS_DUCKDB` / `DATA_DIR` は
`__init__.py` を SoT とし、submodule から `from . import HAS_DUCKDB` で参照する
（テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` 互換）。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path.home() / ".claude" / "rl-anything"

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


# 共通ヘルパは telemetry_query/helpers.py に集約（後方互換のため再エクスポート）
from .helpers import (  # noqa: E402, F401
    _warn_no_duckdb,
    _load_jsonl,
    _filter_by_project,
    _filter_by_time,
    _build_time_where,
    _parse_ts,
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

    sessions_file 未指定: session_store の sessions テーブル (DuckDB) を参照。
    sessions_file 指定:   後方互換のため指定された JSONL を読む（テスト用）。

    Args:
        project: フィルタするプロジェクト名。None の場合は全レコード。
        include_unknown: True の場合、project が null のレコードも含める。
        sessions_file: 明示的に JSONL パスを指定（後方互換、テスト用）。
        since: ISO 8601 文字列。この時刻以降のレコードのみ返す。
        until: ISO 8601 文字列。この時刻より前のレコードのみ返す。
    """
    if sessions_file is not None:
        if not sessions_file.exists():
            return []
        if HAS_DUCKDB:
            return _duckdb_query_file(sessions_file, project=project, include_unknown=include_unknown, since=since, until=until)
        _warn_no_duckdb()
        records = _load_jsonl(sessions_file)
        records = _filter_by_project(records, project, include_unknown)
        return _filter_by_time(records, since, until)

    if HAS_DUCKDB:
        return _query_sessions_table(project=project, include_unknown=include_unknown, since=since, until=until)

    # HAS_DUCKDB=False のフォールバック: 従来通り JSONL を読む
    fallback = DATA_DIR / "sessions.jsonl"
    if not fallback.exists():
        return []
    _warn_no_duckdb()
    records = _load_jsonl(fallback)
    records = _filter_by_project(records, project, include_unknown)
    return _filter_by_time(records, since, until)


def _query_sessions_table(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """session_store の sessions テーブルを参照してレコードを返す。

    raw_json をデコードして元の dict を返す（read_json_auto と同じ形式）。
    """
    import session_store
    if not session_store.SESSIONS_DB.exists():
        return []
    conn = duckdb.connect(str(session_store.SESSIONS_DB), read_only=True)
    try:
        sql = "SELECT raw_json FROM sessions"
        params: Dict[str, Any] = {}
        where_parts: List[str] = []
        if project is not None:
            params["project"] = project
            if include_unknown:
                where_parts.append("(project = $project OR project IS NULL)")
            else:
                where_parts.append("project = $project")
        if since:
            params["since"] = since
            where_parts.append("timestamp >= $since")
        if until:
            params["until"] = until
            where_parts.append("timestamp < $until")
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        rows = conn.execute(sql, params).fetchall()
        results: List[Dict[str, Any]] = []
        for (raw,) in rows:
            try:
                results.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue
        return results
    finally:
        conn.close()


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


# usage / errors / skill counts / skill-session 集計は telemetry_query/usage_errors.py に集約
# （後方互換のため再エクスポート）
from .usage_errors import (  # noqa: E402, F401
    TRACE_WINDOW_MINUTES,
    query_usage,
    query_errors,
    query_skill_counts,
    query_usage_by_skill_session,
    _duckdb_skill_counts,
    _aggregate_skill_sessions,
)
