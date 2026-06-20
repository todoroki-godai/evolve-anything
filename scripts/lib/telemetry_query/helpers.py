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
        "[evolve-anything] duckdb not installed. Falling back to Python JSONL parser. "
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
    *,
    alias_aware: bool = False,
) -> List[Dict[str, Any]]:
    """Python でプロジェクトフィルタリングを適用する（フォールバック用）。

    alias_aware=False（既定）: ``rec["project"] == project`` の exact-match。
    usage/errors は read 層別名を「accept_slug ごとの exact 呼び出しループ」で扱う
    （PR2 / b4dff29）。ここで既定から別名を効かせると、その no-duckdb fallback 経路で
    同一レコードが accept_slug の数だけ二重カウントされるため、既定は exact のまま据える。

    alias_aware=True: PJ rename の旧 slug を ``pj_slug.canonical_pj_slug`` で現 slug に畳んで
    両辺を比較する（rl-anything≡evolve-anything）。sessions の cross-dir union 経路
    （`_query_sessions_via_store`）だけが opt-in する。これは union が全 PJ の cross-dir
    レコードを一度に返し、Python 側で1回だけフィルタするため二重カウントが起きない。
    """
    if project is None:
        return records
    norm = None
    if alias_aware:
        try:
            from pj_slug import canonical_pj_slug as norm  # type: ignore
        except ImportError:
            norm = None
    target = norm(project) if norm else project
    result = []
    for rec in records:
        rec_project = rec.get("project")
        if rec_project is not None and (norm(rec_project) if norm else rec_project) == target:
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
