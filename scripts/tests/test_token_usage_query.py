#!/usr/bin/env python3
"""token_usage_query — TOP-N / WoW / cache hit anomaly テスト。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
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


@pytest.fixture
def query(store, monkeypatch):
    import token_usage_query as tuq
    monkeypatch.setattr(tuq, "_store", store)
    return tuq


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rec(uuid, days_ago: float, pj_id="-pj-a",
         input_tokens=0, output_tokens=0,
         cache_creation=0, cache_read=0, session_id="s1"):
    ts = (_now() - timedelta(days=days_ago)).isoformat()
    return {
        "uuid": uuid,
        "ts": ts,
        "pj_id": pj_id,
        "pj_slug": pj_id.lstrip("-").split("-")[-1],
        "session_id": session_id,
        "parent_uuid": None,
        "is_sidechain": False,
        "model": "claude-sonnet-4-7",
        "role": "assistant",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "web_search_requests": 0,
        "web_fetch_requests": 0,
    }


def test_top_n_basic(store, query):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    store.append_batch([
        _rec("a1", 1, pj_id="-pj-a", input_tokens=5_000_000, cache_read=3_000_000, cache_creation=1_000_000),
        _rec("b1", 1, pj_id="-pj-b", input_tokens=2_000_000),
        _rec("c1", 1, pj_id="-pj-c", input_tokens=500_000),
    ])
    top = query.top_n_consumers(days=30, n=3)
    assert len(top) == 3
    assert top[0]["pj_id"] == "-pj-a"
    assert top[0]["tokens"] == 9_000_000  # input(5M) + output(0) + cache_creation(1M) + cache_read(3M)
    assert top[0]["cache_hit_pct"] is not None and top[0]["cache_hit_pct"] > 70


def test_wow_under_14_days_returns_empty(store, query):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    # 直近 7 日のみデータ → 14 日履歴がないので空
    store.append_batch([
        _rec("a1", 1, pj_id="-pj-a", input_tokens=10_000_000),
        _rec("a2", 3, pj_id="-pj-a", input_tokens=5_000_000),
    ])
    assert query.wow_anomalies() == []


def test_wow_spike_detected(store, query):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    # 前週: 2M tokens, 今週: 10M tokens → +400% spike
    # has_history のため 14 日以上前のレコードも 1 つ必要
    store.append_batch([
        _rec("hist", 20, pj_id="-pj-a", input_tokens=1),  # 履歴の起点 (>=14 days)
        _rec("last1", 10, pj_id="-pj-a", input_tokens=2_000_000),
        _rec("this1", 2, pj_id="-pj-a", input_tokens=10_000_000),
    ])
    res = query.wow_anomalies(min_pct=50.0, min_tokens=1_000_000)
    assert len(res) == 1
    assert res[0]["pj_id"] == "-pj-a"
    assert res[0]["wow_pct"] > 50.0


def test_cache_hit_drop_detected(store, query):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    # last week: hit 80% (cc=200, cr=800)
    # this week: hit 20% (cc=800, cr=200)
    store.append_batch([
        _rec("l1", 10, pj_id="-pj-a", cache_creation=200, cache_read=800),
        _rec("t1", 2,  pj_id="-pj-a", cache_creation=800, cache_read=200),
    ])
    res = query.cache_hit_anomalies()
    assert len(res) == 1
    assert res[0]["pj_id"] == "-pj-a"
    assert res[0]["this_hit_pct"] < 40.0
    assert res[0]["last_hit_pct"] >= 60.0
    assert res[0]["drop_pt"] >= 20.0


@pytest.mark.parametrize("by", ["session", "model", "week"])
def test_pj_breakdown(store, query, by):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    store.append_batch([
        _rec("u1", 1, pj_id="-pj-a", session_id="s1", input_tokens=100),
        _rec("u2", 2, pj_id="-pj-a", session_id="s2", input_tokens=200),
    ])
    rows = query.pj_breakdown("-pj-a", by=by, limit=10)
    assert len(rows) >= 1
    for r in rows:
        assert "key" in r and "tokens" in r
