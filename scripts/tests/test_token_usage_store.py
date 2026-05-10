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


def _rec(uuid: str, ts: str = "2026-05-01T12:00:00Z", pj_id: str = "-pj-rl-anything", **kw) -> dict:
    base = {
        "uuid": uuid,
        "ts": ts,
        "pj_id": pj_id,
        "pj_slug": "rl-anything",
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
    assert store.get_last_ingested_ts("-pj-rl-anything") is None
    store.append_batch([
        _rec("u1", ts="2026-05-01T12:00:00Z"),
        _rec("u2", ts="2026-05-03T08:00:00Z"),
        _rec("u3", ts="2026-05-02T12:00:00Z"),
    ])
    last = store.get_last_ingested_ts("-pj-rl-anything")
    assert last is not None
    assert "2026-05-03" in last
    # 別 PJ は None
    assert store.get_last_ingested_ts("-pj-other") is None
