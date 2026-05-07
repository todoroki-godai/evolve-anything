#!/usr/bin/env python3
"""trigger_engine.py の DuckDB クエリパス テスト。

HAS_DUCKDB=True 時と False 時の両方で _count_sessions_since が正しい値を返すことを検証。
DuckDB は read_json_auto() で sessions.jsonl を直接クエリする（スキーマ不要）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = str(_REPO_ROOT / "scripts" / "lib")


def _run_count(tmp_path: Path, last_run: str, mock_no_duckdb: bool = False) -> dict:
    """subprocess で _count_sessions_since を実行し結果を返す。"""
    sessions_file = tmp_path / "sessions.jsonl"
    # 3 つのユニークセッション（abc x2 重複、def、ghi）を作成
    records = [
        {"session_id": "abc", "timestamp": "2026-01-01T12:00:00+00:00"},
        {"session_id": "def", "timestamp": "2026-01-01T18:00:00+00:00"},
        {"session_id": "abc", "timestamp": "2026-01-01T13:00:00+00:00"},  # 重複
        {"session_id": "ghi", "timestamp": "2026-01-01T20:00:00+00:00"},
    ]
    sessions_file.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )

    patch = "mock.patch('trigger_engine.HAS_DUCKDB', False); " if mock_no_duckdb else ""
    code = (
        "import sys, json, os; "
        f"sys.path.insert(0, {_LIB!r}); "
        "import trigger_engine as te; "
        + (f"te.HAS_DUCKDB = False; " if mock_no_duckdb else "")
        + f"result = te._count_sessions_since({last_run!r}); "
        "print(json.dumps({'count': result}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return json.loads(result.stdout.strip())


class TestCountSessionsSinceDuckDB:
    def test_duckdb_path_counts_unique_sessions(self, tmp_path):
        """DuckDB パスで last_run より後のユニークセッション数を返す。"""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            pytest.skip("duckdb not installed")

        last_run = "2026-01-01T00:00:00+00:00"
        data = _run_count(tmp_path, last_run, mock_no_duckdb=False)
        # abc, def, ghi の 3 ユニーク（abc 重複除く）
        assert data["count"] == 3

    def test_jsonl_fallback_counts_unique_sessions(self, tmp_path):
        """DuckDB 無効化時もユニークセッション数を正しく返す。"""
        last_run = "2026-01-01T00:00:00+00:00"
        data = _run_count(tmp_path, last_run, mock_no_duckdb=True)
        assert data["count"] == 3

    def test_last_run_filter_excludes_old_sessions(self, tmp_path):
        """last_run より前のセッションは除外される。"""
        sessions_file = tmp_path / "sessions.jsonl"
        records = [
            {"session_id": "old", "timestamp": "2025-12-31T23:59:59+00:00"},  # before
            {"session_id": "new", "timestamp": "2026-01-02T00:00:00+00:00"},  # after
        ]
        sessions_file.write_text(
            "\n".join(json.dumps(r) for r in records), encoding="utf-8"
        )

        code = (
            "import sys, json, os; "
            f"sys.path.insert(0, {_LIB!r}); "
            "import trigger_engine as te; te.HAS_DUCKDB = False; "
            "result = te._count_sessions_since('2026-01-01T00:00:00+00:00'); "
            "print(json.dumps({'count': result}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data["count"] == 1  # "new" のみ

    def test_empty_sessions_returns_zero(self, tmp_path):
        """sessions.jsonl が空の場合は 0 を返す。"""
        (tmp_path / "sessions.jsonl").write_text("", encoding="utf-8")
        code = (
            "import sys, json, os; "
            f"sys.path.insert(0, {_LIB!r}); "
            "import trigger_engine as te; te.HAS_DUCKDB = False; "
            "result = te._count_sessions_since('2026-01-01T00:00:00+00:00'); "
            "print(json.dumps({'count': result}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data["count"] == 0

    def test_missing_file_returns_zero(self, tmp_path):
        """sessions.jsonl が存在しない場合は 0 を返す。"""
        code = (
            "import sys, json, os; "
            f"sys.path.insert(0, {_LIB!r}); "
            "import trigger_engine as te; te.HAS_DUCKDB = False; "
            "result = te._count_sessions_since('2026-01-01T00:00:00+00:00'); "
            "print(json.dumps({'count': result}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert data["count"] == 0
