"""telemetry_query 共通ヘルパ。

警告出力 / JSONL ローダ / Python 側フィルタ / DuckDB 用 WHERE 句生成 / ISO 8601 パース。
`HAS_DUCKDB` は package (`__init__.py`) を SoT とし、submodule 関数内で
`from . import HAS_DUCKDB` 経由で参照する（テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` 互換）。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _warn_no_duckdb() -> None:
    print(
        "[rl-anything] duckdb not installed. Falling back to Python JSONL parser. "
        "Install with: pip install duckdb",
        file=sys.stderr,
    )


def _load_jsonl(filepath: Path) -> List[Dict[str, Any]]:
    """JSONL ファイルを Python で読み込む（フォールバック用）。"""
    if not filepath.exists():
        return []
    records = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _filter_by_project(
    records: List[Dict[str, Any]],
    project: Optional[str],
    include_unknown: bool = False,
) -> List[Dict[str, Any]]:
    """Python でプロジェクトフィルタリングを適用する（フォールバック用）。"""
    if project is None:
        return records
    result = []
    for rec in records:
        rec_project = rec.get("project")
        if rec_project == project:
            result.append(rec)
        elif include_unknown and rec_project is None:
            result.append(rec)
    return result


def _filter_by_time(
    records: List[Dict[str, Any]],
    since: Optional[str],
    until: Optional[str],
    timestamp_field: str = "timestamp",
) -> List[Dict[str, Any]]:
    """Python で時間範囲フィルタリングを適用する（フォールバック用）。"""
    if since is None and until is None:
        return records
    result = []
    for rec in records:
        ts = rec.get(timestamp_field, "")
        if not ts:
            continue
        if since and ts < since:
            continue
        if until and ts >= until:
            continue
        result.append(rec)
    return result


def _build_time_where(
    since: Optional[str], until: Optional[str], params: Dict[str, Any],
    timestamp_field: str = "timestamp",
) -> str:
    """since/until の WHERE 句断片を生成する。"""
    clauses = []
    if since:
        params["since"] = since
        clauses.append(f"{timestamp_field} >= $since")
    if until:
        params["until"] = until
        clauses.append(f"{timestamp_field} < $until")
    return " AND ".join(clauses)


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """ISO 8601 タイムスタンプ文字列を datetime に変換する。"""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
