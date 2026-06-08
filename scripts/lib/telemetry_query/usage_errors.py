"""usage / errors / skill counts / skill-session 集計のクエリ層。

`HAS_DUCKDB` は package (`__init__.py`) を SoT とするため、submodule 関数内で
`from . import HAS_DUCKDB, DATA_DIR` 経由で参照する
（テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` 互換）。
"""
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from rl_common import hook_store_path

from .helpers import (
    _warn_no_duckdb,
    _load_jsonl,
    _filter_by_project,
    _filter_by_time,
    _parse_ts,
)

TRACE_WINDOW_MINUTES = 5


def query_usage(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    usage_file: Optional[Path] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """usage.jsonl をクエリして結果を返す。

    Args:
        project: フィルタするプロジェクト名。None の場合は全レコード。
        include_unknown: True の場合、project が null のレコードも含める。
        usage_file: usage.jsonl のパス（テスト用）。
        since: ISO 8601 文字列。この時刻以降のレコードのみ返す。
        until: ISO 8601 文字列。この時刻より前のレコードのみ返す。
    """
    from . import DATA_DIR, HAS_DUCKDB, _duckdb_query_file

    filepath = usage_file or hook_store_path("usage.jsonl", base=DATA_DIR)
    if not filepath.exists():
        return []

    if HAS_DUCKDB:
        return _duckdb_query_file(filepath, project=project, include_unknown=include_unknown, since=since, until=until, timestamp_field="ts")

    _warn_no_duckdb()
    records = _load_jsonl(filepath)
    records = _filter_by_project(records, project, include_unknown)
    return _filter_by_time(records, since, until, timestamp_field="ts")


def query_errors(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    errors_file: Optional[Path] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """errors.jsonl をクエリして結果を返す。"""
    from . import DATA_DIR, HAS_DUCKDB, _duckdb_query_file

    filepath = errors_file or (DATA_DIR / "errors.jsonl")
    if not filepath.exists():
        return []

    if HAS_DUCKDB:
        return _duckdb_query_file(filepath, project=project, include_unknown=include_unknown, since=since, until=until)

    _warn_no_duckdb()
    records = _load_jsonl(filepath)
    records = _filter_by_project(records, project, include_unknown)
    return _filter_by_time(records, since, until)


def query_skill_counts(
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    min_count: int = 1,
    usage_file: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """スキル別の使用回数を集計して返す。

    Returns:
        [{"skill_name": str, "count": int}, ...]
    """
    from . import DATA_DIR, HAS_DUCKDB

    filepath = usage_file or hook_store_path("usage.jsonl", base=DATA_DIR)
    if not filepath.exists():
        return []

    if HAS_DUCKDB:
        return _duckdb_skill_counts(filepath, project=project, include_unknown=include_unknown, min_count=min_count)

    _warn_no_duckdb()
    records = _load_jsonl(filepath)
    filtered = _filter_by_project(records, project, include_unknown)
    counts: Dict[str, int] = {}
    for rec in filtered:
        skill = rec.get("skill_name", "")
        if skill:
            counts[skill] = counts.get(skill, 0) + 1
    return [
        {"skill_name": skill, "count": count}
        for skill, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)
        if count >= min_count
    ]


def _duckdb_skill_counts(
    filepath: Path,
    *,
    project: Optional[str] = None,
    include_unknown: bool = False,
    min_count: int = 1,
) -> List[Dict[str, Any]]:
    """DuckDB でスキル別カウントを集計する。"""
    import duckdb

    conn = duckdb.connect()
    try:
        read_expr = f"read_json_auto('{filepath}', ignore_errors=true)"
        where_sql = ""
        params: Dict[str, Any] = {}

        if project is not None:
            # project カラム存在チェック
            cols_cursor = conn.execute(f"SELECT column_name FROM (DESCRIBE SELECT * FROM {read_expr})")
            col_names = {row[0] for row in cols_cursor.fetchall()}

            if "project" not in col_names:
                if not include_unknown:
                    return []
                # include_unknown の場合は全レコード対象
            else:
                params["project"] = project
                if include_unknown:
                    where_sql = " WHERE (project = $project OR project IS NULL)"
                else:
                    where_sql = " WHERE project = $project"

        sql = (
            f"SELECT skill_name, COUNT(*) as count "
            f"FROM {read_expr}{where_sql} "
            f"GROUP BY skill_name HAVING COUNT(*) >= {min_count} "
            f"ORDER BY count DESC"
        )

        result = conn.execute(sql, params).fetchall()
        return [{"skill_name": row[0], "count": row[1]} for row in result]
    finally:
        conn.close()


def query_usage_by_skill_session(
    skill_name: str,
    *,
    project: Optional[str] = None,
    usage_file: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """スキル単位でセッションごとのツール使用統計を返す。

    1. usage.jsonl から全レコードを取得
    2. Skill ツール呼び出し（skill_name フィールドあり）を検出
    3. 指定 skill_name のレコードを session_id でグループ化
    4. 各グループ内で、Skill 発火後〜min(次のSkill発火, TRACE_WINDOW_MINUTES分後) の
       ツール呼び出しを集計
    5. 返り値: [{"session_id": str, "tool_calls": int, "read_edit_cycles": int,
                "errors": int, "duration_seconds": float}, ...]
    """
    from . import DATA_DIR, HAS_DUCKDB, _duckdb_query_file

    filepath = usage_file or hook_store_path("usage.jsonl", base=DATA_DIR)
    if not filepath.exists():
        return []

    # DuckDB / Python フォールバック共通で全レコード取得してから Python で集計
    # （ウィンドウ計算が複雑なため Python で統一）
    if HAS_DUCKDB:
        records = _duckdb_query_file(filepath, project=project)
    else:
        records = _load_jsonl(filepath)
        records = _filter_by_project(records, project)

    return _aggregate_skill_sessions(records, skill_name)


def _aggregate_skill_sessions(
    records: List[Dict[str, Any]], skill_name: str,
) -> List[Dict[str, Any]]:
    """レコードリストからスキル単位のセッション統計を集計する。"""
    by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        sid = rec.get("session_id")
        if sid:
            by_session[sid].append(rec)

    results = []
    window_seconds = TRACE_WINDOW_MINUTES * 60

    for sid, session_records in by_session.items():
        sorted_recs = sorted(session_records, key=lambda r: r.get("ts", r.get("timestamp", "")))

        skill_fires = []
        for i, rec in enumerate(sorted_recs):
            if rec.get("tool_name") == "Skill" and rec.get("skill_name") == skill_name:
                skill_fires.append((i, rec))

        if not skill_fires:
            continue

        for fire_idx, (pos, fire_rec) in enumerate(skill_fires):
            fire_ts = _parse_ts(fire_rec.get("ts", fire_rec.get("timestamp", "")))
            if fire_ts is None:
                continue

            next_skill_ts = None
            for j in range(pos + 1, len(sorted_recs)):
                if sorted_recs[j].get("tool_name") == "Skill":
                    next_skill_ts = _parse_ts(sorted_recs[j].get("ts", sorted_recs[j].get("timestamp", "")))
                    break

            window_end_ts = fire_ts.timestamp() + window_seconds
            if next_skill_ts is not None:
                window_end_ts = min(window_end_ts, next_skill_ts.timestamp())

            tool_calls = 0
            errors = 0
            read_edit_cycles = 0
            last_was_read = False
            last_ts_str = fire_rec.get("ts", fire_rec.get("timestamp", ""))

            for j in range(pos + 1, len(sorted_recs)):
                rec = sorted_recs[j]
                rec_ts = _parse_ts(rec.get("ts", rec.get("timestamp", "")))
                if rec_ts is None:
                    continue
                if rec_ts.timestamp() >= window_end_ts:
                    break

                tool_calls += 1
                last_ts_str = rec.get("ts", rec.get("timestamp", last_ts_str))

                if rec.get("error"):
                    errors += 1

                tool = rec.get("tool_name", "")
                if tool == "Read":
                    last_was_read = True
                elif tool == "Edit" and last_was_read:
                    read_edit_cycles += 1
                    last_was_read = False
                else:
                    last_was_read = False

            last_rec_ts = _parse_ts(last_ts_str)
            duration = 0.0
            if last_rec_ts and fire_ts:
                duration = max(0.0, last_rec_ts.timestamp() - fire_ts.timestamp())

            results.append({
                "session_id": sid,
                "tool_calls": tool_calls,
                "read_edit_cycles": read_edit_cycles,
                "errors": errors,
                "duration_seconds": duration,
            })

    return results
