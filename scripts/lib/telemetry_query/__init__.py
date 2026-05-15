"""DuckDB ベースのテレメトリクエリ層。

JSONL ファイルを DuckDB の read_json_auto() で直接 SQL クエリする。
DuckDB 未インストール時は load_jsonl() + Python フィルタにフォールバック。

Phase 11 で `telemetry_query.py` (652 行) を package 化（Slice 1-3 で 4 サブモジュールに分割）:
- `helpers.py` — 警告・JSONL ローダ・Python フィルタ・WHERE 句生成・ISO8601 パース
- `usage_errors.py` — usage / errors / skill counts / skill-session 集計
- `sessions_corrections_workflows.py` — sessions / corrections / workflows + `_duckdb_query_file`

`HAS_DUCKDB` / `DATA_DIR` は本ファイル (`__init__.py`) を SoT とし、submodule からは
`from . import HAS_DUCKDB, DATA_DIR` で関数内 lazy 参照する設計
（テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` 14 箇所互換）。
"""
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "rl-anything"

try:
    import duckdb  # noqa: F401  # HAS_DUCKDB 判定のための probe
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


# sessions / corrections / workflows + 汎用 DuckDB JSONL ローダは
# telemetry_query/sessions_corrections_workflows.py に集約（後方互換のため再エクスポート）
from .sessions_corrections_workflows import (  # noqa: E402, F401
    query_sessions,
    _query_sessions_table,
    _duckdb_query_file,
    query_corrections,
    _filter_corrections_by_project,
    _duckdb_query_corrections,
    query_workflows,
    _duckdb_query_workflows,
)


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
