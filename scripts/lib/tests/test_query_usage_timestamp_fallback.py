"""telemetry_query.query_usage の since/until 窓フィルタが旧スキーマ行を取りこぼさないこと
のテスト（#480 item 5）。

usage.jsonl は現行スキーマ ``ts`` と旧スキーマ（backfill 由来）``timestamp`` が混在する
（実データで 261 件）。``query_usage`` は ``timestamp_field="ts"`` 固定で since/until
フィルタしていたため、since/until を指定する呼び出し（skill_evolve/telemetry_scoring.py 等）
で旧行が黙って窓の外へ落ちていた。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from telemetry_query.usage_errors import query_usage  # noqa: E402


def _write_jsonl(path: Path, records) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


_RECORDS = [
    # 現行スキーマ（ts）
    {"skill_name": "evolve", "ts": "2026-08-15T10:00:00Z", "session_id": "s1"},
    # 旧スキーマ（timestamp のみ・ts 無し）
    {"skill_name": "research-best-practices", "timestamp": "2026-08-15T11:00:00Z", "session_id": "s2"},
    # 窓の外（旧スキーマ）
    {"skill_name": "old", "timestamp": "2026-01-01T00:00:00Z", "session_id": "s3"},
]


def test_python_fallback_includes_legacy_timestamp_keyed_rows_in_since_window(tmp_path, monkeypatch):
    monkeypatch.setattr("telemetry_query.HAS_DUCKDB", False)
    usage_file = tmp_path / "usage.jsonl"
    _write_jsonl(usage_file, _RECORDS)

    result = query_usage(usage_file=usage_file, since="2026-08-01T00:00:00Z")

    names = {r["skill_name"] for r in result}
    assert names == {"evolve", "research-best-practices"}
    assert "old" not in names


def test_duckdb_path_includes_legacy_timestamp_keyed_rows_in_since_window(tmp_path):
    duckdb = __import__("importlib").util.find_spec("duckdb")
    if duckdb is None:
        import pytest
        pytest.skip("duckdb not installed")

    usage_file = tmp_path / "usage.jsonl"
    _write_jsonl(usage_file, _RECORDS)

    result = query_usage(usage_file=usage_file, since="2026-08-01T00:00:00Z")

    names = {r["skill_name"] for r in result}
    assert names == {"evolve", "research-best-practices"}
    assert "old" not in names
