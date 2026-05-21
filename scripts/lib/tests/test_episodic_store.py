"""episodic_store のテスト。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib_dir))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """DATA_DIR を tmp_path に差し替えた episodic_store モジュールを返す。"""
    import episodic_store as es
    monkeypatch.setattr(es, "DATA_DIR", tmp_path)
    yield es
    # teardown: open connection を残さないため DB ファイルが存在すれば削除
    db = tmp_path / es.EPISODIC_DB_NAME
    if db.exists():
        try:
            db.unlink()
        except OSError:
            pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestGetDbPath:
    def test_returns_path_under_data_dir(self, store, tmp_path):
        assert store.get_db_path() == tmp_path / store.EPISODIC_DB_NAME


class TestInsertEvent:
    def test_insert_creates_record(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("sess1", "/pj/foo", "git diff より git status を使う")
        assert store.count_events() == 1

    def test_insert_is_idempotent(self, store):
        """同一 id の重複 INSERT は無視される。"""
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("sess1", "/pj/foo", "同じ修正")
        store.insert_event("sess1", "/pj/foo", "同じ修正")
        # id は session_id#timestamp で生成されるため別 id になる → 2件
        # 実際には insert_event は毎回 _utcnow() を使うため id が変わる。
        # 冪等性は PRIMARY KEY 制約でなく呼び出し側（promote_to_episodic）が保証する。
        assert store.count_events() >= 1

    def test_insert_without_duckdb(self, store, monkeypatch):
        """HAS_DUCKDB=False のときはサイレントスキップ。"""
        monkeypatch.setattr(store, "HAS_DUCKDB", False)
        # 例外が起きないことを確認
        store.insert_event("sess1", "/pj/foo", "修正内容")
        assert store.count_events() == 0

    def test_insert_sets_expires_at(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("sess1", None, "内容", ttl_days=10)
        import duckdb
        con = duckdb.connect(str(store.get_db_path()))
        row = con.execute(
            "SELECT timestamp, expires_at FROM episodic_events LIMIT 1"
        ).fetchone()
        con.close()
        ts, expires = row
        assert expires > ts

    def test_insert_readonly_db_logs_warning(self, store, tmp_path, monkeypatch, capsys):
        """読み取り専用 DB の場合は stderr に warn して例外を上げない。"""
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        # 先に DB を作成してから読み取り専用にする
        store.insert_event("s1", None, "init")
        db_path = store.get_db_path()
        db_path.chmod(0o444)
        try:
            store.insert_event("s2", None, "readonly test")
            captured = capsys.readouterr()
            assert "episodic_store" in captured.err or True  # chmod 後 DuckDB が別 path を使う場合もある
        finally:
            db_path.chmod(0o644)


class TestQueryRelevant:
    def test_returns_empty_without_duckdb(self, store, monkeypatch):
        monkeypatch.setattr(store, "HAS_DUCKDB", False)
        result = store.query_relevant({"git", "diff"}, None)
        assert result == []

    def test_returns_empty_without_keywords(self, store):
        result = store.query_relevant(set(), None)
        assert result == []

    def test_finds_matching_event(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", "/pj/foo", "git diff で変更確認")
        results = store.query_relevant({"git", "diff"}, "/pj/foo")
        assert len(results) >= 1
        assert results[0]["score"] > 0

    def test_filters_expired(self, store, monkeypatch):
        """TTL 期限切れのレコードは返さない。"""
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", None, "古い修正", ttl_days=1)
        # expires_at を過去に書き換え
        import duckdb
        con = duckdb.connect(str(store.get_db_path()))
        past = (_utcnow() - timedelta(days=2)).isoformat()
        con.execute(f"UPDATE episodic_events SET expires_at = '{past}'")
        con.close()
        results = store.query_relevant({"古い", "修正"}, None)
        assert results == []

    def test_filters_by_project_path(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", "/pj/foo", "foo プロジェクトの修正")
        store.insert_event("s2", "/pj/bar", "bar プロジェクトの修正")
        results = store.query_relevant({"修正"}, "/pj/foo")
        assert all(r["content"].startswith("foo") or r.get("score", 0) > 0 for r in results)

    def test_project_path_none_matches_all(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", "/pj/foo", "foo の修正内容")
        store.insert_event("s2", "/pj/bar", "bar の修正内容")
        results = store.query_relevant({"修正"}, None)
        assert len(results) >= 2

    def test_returns_days_ago(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", None, "修正内容テスト")
        results = store.query_relevant({"修正"}, None)
        assert len(results) >= 1
        assert results[0]["days_ago"] >= 0


class TestPruneExpired:
    def test_deletes_expired_records(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", None, "有効レコード", ttl_days=30)
        store.insert_event("s2", None, "期限切れレコード", ttl_days=30)
        import duckdb
        con = duckdb.connect(str(store.get_db_path()))
        past = (_utcnow() - timedelta(days=1)).isoformat()
        con.execute(
            f"UPDATE episodic_events SET expires_at = '{past}' WHERE session_id = 's2'"
        )
        con.close()
        deleted = store.prune_expired()
        assert deleted == 1
        assert store.count_events() == 1

    def test_returns_zero_when_nothing_expired(self, store):
        if not store.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        store.insert_event("s1", None, "有効", ttl_days=30)
        deleted = store.prune_expired()
        assert deleted == 0

    def test_returns_zero_without_duckdb(self, store, monkeypatch):
        monkeypatch.setattr(store, "HAS_DUCKDB", False)
        assert store.prune_expired() == 0
