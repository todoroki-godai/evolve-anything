#!/usr/bin/env python3
"""TokenUsageStore — DuckDB SoR + INSERT OR IGNORE 冪等性テスト。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = str(_REPO_ROOT / "scripts" / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)


@pytest.fixture
def store(tmp_path, monkeypatch):
    import token_usage_store as tus
    monkeypatch.setattr(tus, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tus, "USAGE_DB", tmp_path / "token_usage.db")
    monkeypatch.setattr(tus, "USAGE_JSONL", tmp_path / "token_usage.jsonl")
    return tus


def _rec(uuid: str, ts: str = "2026-05-01T12:00:00Z", pj_id: str = "-pj-evolve-anything", **kw) -> dict:
    base = {
        "uuid": uuid,
        "ts": ts,
        "pj_id": pj_id,
        "pj_slug": "evolve-anything",
        "session_id": "sess-1",
        "parent_uuid": None,
        "is_sidechain": False,
        "model": "claude-sonnet-4-7",
        "role": "assistant",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 800,
        "web_search_requests": 0,
        "web_fetch_requests": 0,
    }
    base.update(kw)
    return base


def test_append_batch_basic(store):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    inserted = store.append_batch([_rec("u1"), _rec("u2", ts="2026-05-02T12:00:00Z")])
    assert inserted == 2
    rows = store.query("SELECT uuid FROM token_usage ORDER BY uuid")
    assert [r[0] for r in rows] == ["u1", "u2"]


def test_query_does_not_require_write_access(store):
    """#65: read 経路（query・con 未指定）は write 権限を要求してはならない（read_only 接続）。

    旧挙動は ``query`` が ``_connect()``（read-write + CREATE TABLE DDL）で自己接続し、実 DB の
    初回 read-write open が write transaction commit でファイル byte を書き換えた（dry-run byte
    契約 #461 違反・dogfood Layer 1a 赤）。read_only 自己接続に揃えて根治する。

    判別方法: db を chmod 444 にすると read-write 自己接続は EACCES で失敗、read_only 自己接続は
    成功して読める。SHA 比較はクリーンな小 DB が既に canonical 形のため退行を捕捉できない（実 DB の
    初回 fold 状態でしか SHA が動かない）ので、write 権限要求の有無で判別する。
    """
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    store.append_batch([_rec("u1"), _rec("u2", ts="2026-05-02T12:00:00Z")])
    assert store.USAGE_DB.exists()
    store.USAGE_DB.chmod(0o444)
    try:
        rows = store.query("SELECT COUNT(*) FROM token_usage")
        assert rows[0][0] == 2, "read_only でない（444 db を読めず＝write 権限要求）"
    finally:
        store.USAGE_DB.chmod(0o644)


def test_append_batch_idempotent(store):
    """同じ uuid を 2 回 append しても行数不変 (CRITICAL)。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    recs = [_rec("u1"), _rec("u2", ts="2026-05-02T12:00:00Z")]
    first = store.append_batch(recs)
    second = store.append_batch(recs)
    assert first == 2
    assert second == 0  # 重複は INSERT OR IGNORE でスキップ
    rows = store.query("SELECT COUNT(*) FROM token_usage")
    assert rows[0][0] == 2


def test_get_last_ingested_ts(store):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    assert store.get_last_ingested_ts("-pj-evolve-anything") is None
    store.append_batch([
        _rec("u1", ts="2026-05-01T12:00:00Z"),
        _rec("u2", ts="2026-05-03T08:00:00Z"),
        _rec("u3", ts="2026-05-02T12:00:00Z"),
    ])
    last = store.get_last_ingested_ts("-pj-evolve-anything")
    assert last is not None
    assert "2026-05-03" in last
    # 別 PJ は None
    assert store.get_last_ingested_ts("-pj-other") is None
