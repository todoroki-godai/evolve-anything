#!/usr/bin/env python3
"""SessionStore の Repository pattern テスト。

DuckDB が SoR、HAS_DUCKDB=False 時は JSONL フォールバック。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = str(_REPO_ROOT / "scripts" / "lib")


def _import_session_store(env: dict[str, str] | None = None) -> dict:
    """subprocess で session_store を import して属性を返す。"""
    code = (
        "import sys, json; "
        f"sys.path.insert(0, {_LIB!r}); "
        "import session_store as ss; "
        "print(json.dumps({"
        "'data_dir': str(ss.DATA_DIR), "
        "'has_duckdb': ss.HAS_DUCKDB, "
        "'sessions_db': str(ss.SESSIONS_DB), "
        "'sessions_jsonl': str(ss.SESSIONS_JSONL)"
        "}))"
    )
    merged = {**os.environ, **(env or {})}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=merged)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """各テスト独立の SessionStore インスタンス。

    monkeypatch.setattr で DATA_DIR / SESSIONS_DB / SESSIONS_JSONL を一時パッチ。
    teardown で自動復元されるため、他テストへ汚染しない。
    """
    sys.path.insert(0, _LIB)
    import session_store
    monkeypatch.setattr(session_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(session_store, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(session_store, "SESSIONS_JSONL", tmp_path / "sessions.jsonl")
    return session_store


class TestPaths:
    def test_default_data_dir(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
        result = _import_session_store()
        assert result["data_dir"] == str(Path.home() / ".claude" / "rl-anything")

    def test_env_override(self, tmp_path):
        result = _import_session_store({"CLAUDE_PLUGIN_DATA": str(tmp_path)})
        assert result["data_dir"] == str(tmp_path)
        assert result["sessions_db"] == str(tmp_path / "sessions.db")
        assert result["sessions_jsonl"] == str(tmp_path / "sessions.jsonl")


class TestAppend:
    def test_append_writes_to_db_when_duckdb_available(self, fresh_store, tmp_path):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        record = {
            "session_id": "abc",
            "timestamp": "2026-04-30T12:00:00+00:00",
            "project": "test-project",
            "skill_count": 5,
            "error_count": 0,
        }
        fresh_store.append(record)

        # DB から直接確認
        import duckdb
        con = duckdb.connect(str(tmp_path / "sessions.db"))
        result = con.execute("SELECT session_id, project, skill_count FROM sessions").fetchall()
        con.close()
        assert len(result) == 1
        assert result[0][0] == "abc"
        assert result[0][1] == "test-project"
        assert result[0][2] == 5

    def test_append_does_not_write_jsonl_when_duckdb_available(self, fresh_store, tmp_path):
        """Phase 2: HAS_DUCKDB=True なら JSONL は書かない（dual-write 停止）。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        record = {"session_id": "duckdb-only", "timestamp": "2026-04-30T12:00:00+00:00"}
        fresh_store.append(record)
        jsonl = tmp_path / "sessions.jsonl"
        assert not jsonl.exists(), "DuckDB 利用時は JSONL を書かない"

    def test_append_falls_back_to_jsonl_without_duckdb(self, fresh_store, tmp_path, monkeypatch):
        monkeypatch.setattr(fresh_store, "HAS_DUCKDB", False)
        record = {
            "session_id": "xyz",
            "timestamp": "2026-04-30T12:00:00+00:00",
            "project": "test",
        }
        fresh_store.append(record)
        jsonl = tmp_path / "sessions.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["session_id"] == "xyz"

    def test_append_handles_minimal_record(self, fresh_store):
        """timestamp と session_id は必須だが、その他は任意。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        record = {"session_id": "min", "timestamp": "2026-04-30T00:00:00+00:00"}
        fresh_store.append(record)  # should not raise


class TestCountUniqueSince:
    def test_counts_distinct_sessions_after_timestamp(self, fresh_store):
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"},
            {"session_id": "a", "timestamp": "2026-04-30T14:00:00+00:00"},  # 重複
            {"session_id": "c", "timestamp": "2026-04-30T15:00:00+00:00"},
        ]
        for r in records:
            fresh_store.append(r)
        count = fresh_store.count_unique_since("2026-04-30T00:00:00+00:00")
        assert count == 3  # a, b, c の 3 ユニーク

    def test_filters_old_sessions(self, fresh_store):
        records = [
            {"session_id": "old", "timestamp": "2025-12-31T00:00:00+00:00"},
            {"session_id": "new", "timestamp": "2026-04-30T00:00:00+00:00"},
        ]
        for r in records:
            fresh_store.append(r)
        count = fresh_store.count_unique_since("2026-01-01T00:00:00+00:00")
        assert count == 1

    def test_empty_returns_zero(self, fresh_store):
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 0


class TestQuery:
    def test_returns_all_sessions(self, fresh_store):
        for i in range(3):
            fresh_store.append({
                "session_id": f"s{i}",
                "timestamp": f"2026-04-30T{12+i:02d}:00:00+00:00",
            })
        results = fresh_store.query()
        assert len(results) == 3

    def test_since_filter(self, fresh_store):
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"},
        ]
        for r in records:
            fresh_store.append(r)
        results = fresh_store.query(since="2026-04-30T12:30:00+00:00")
        assert len(results) == 1
        assert results[0]["session_id"] == "b"

    def test_limit(self, fresh_store):
        for i in range(10):
            fresh_store.append({
                "session_id": f"s{i}",
                "timestamp": f"2026-04-30T{i:02d}:00:00+00:00",
            })
        results = fresh_store.query(limit=3)
        assert len(results) == 3


class TestMigration:
    def test_migrate_jsonl_to_db_imports_all_records(self, fresh_store, tmp_path):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        jsonl = tmp_path / "sessions.jsonl"
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "project": "p1"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "project": "p2"},
            {"session_id": "c", "timestamp": "2026-04-30T14:00:00+00:00"},
        ]
        jsonl.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        imported = fresh_store.migrate_from_jsonl()
        assert imported == 3
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 3

    def test_migrate_is_idempotent(self, fresh_store, tmp_path):
        """同じデータを 2 回 migrate しても重複しない。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        jsonl = tmp_path / "sessions.jsonl"
        records = [{"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"}]
        jsonl.write_text(json.dumps(records[0]), encoding="utf-8")

        fresh_store.migrate_from_jsonl()
        fresh_store.migrate_from_jsonl()  # idempotent
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 1

    def test_migrate_skips_when_db_already_has_data(self, fresh_store, tmp_path):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        # まず DB にデータを入れる
        fresh_store.append({"session_id": "db-only", "timestamp": "2026-04-30T12:00:00+00:00"})
        # JSONL も用意
        jsonl = tmp_path / "sessions.jsonl"
        jsonl.write_text(json.dumps({"session_id": "jsonl-only", "timestamp": "2026-04-30T13:00:00+00:00"}), encoding="utf-8")

        # 自動マイグレーションは sessions.db が空でない場合スキップ
        imported = fresh_store.migrate_from_jsonl(skip_if_db_has_data=True)
        assert imported == 0
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 1


class TestDeleteBySessionIds:
    def test_deletes_matching_session_ids(self, fresh_store):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        for sid in ("a", "b", "c"):
            fresh_store.append({"session_id": sid, "timestamp": "2026-04-30T12:00:00+00:00"})
        deleted = fresh_store.delete_by_session_ids(["a", "c"])
        assert deleted == 2
        remaining = fresh_store.query()
        assert {r["session_id"] for r in remaining} == {"b"}

    def test_source_filter_only_deletes_matching_source(self, fresh_store):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "source": "backfill"})
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "source": "live"})
        deleted = fresh_store.delete_by_session_ids(["a", "b"], source="backfill")
        assert deleted == 1
        remaining = fresh_store.query()
        assert {r["session_id"] for r in remaining} == {"b"}

    def test_empty_ids_returns_zero(self, fresh_store):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        assert fresh_store.delete_by_session_ids([]) == 0
        assert len(fresh_store.query()) == 1

    def test_jsonl_fallback_when_no_duckdb(self, fresh_store, tmp_path, monkeypatch):
        monkeypatch.setattr(fresh_store, "HAS_DUCKDB", False)
        jsonl = tmp_path / "sessions.jsonl"
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "source": "backfill"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "source": "live"},
            {"session_id": "c", "timestamp": "2026-04-30T14:00:00+00:00", "source": "backfill"},
        ]
        jsonl.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        deleted = fresh_store.delete_by_session_ids(["a", "c"], source="backfill")
        assert deleted == 2
        remaining = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert [r["session_id"] for r in remaining] == ["b"]
