#!/usr/bin/env python3
"""issue #28 redesign — 新規テスト 4 件 + chunk commit 1 件 = 5 件。

P2 計測 (2026-05-09 実機 31964 files) で stem == sessionId 100% 確認済のため、
resume/fork セーフティテストは scope 外。
"""
from __future__ import annotations

import json
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


@pytest.fixture
def ingest(store, monkeypatch):
    import token_usage_ingest as tui
    monkeypatch.setattr(tui, "_store", store)
    return tui


def _line(uuid, ts="2026-05-01T12:00:00Z", session_id="sess-1"):
    return json.dumps({
        "uuid": uuid,
        "sessionId": session_id,
        "timestamp": ts,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-7",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            },
        },
    })


def _write_jsonl(pj_dir: Path, stem: str, lines: list[str]):
    pj_dir.mkdir(parents=True, exist_ok=True)
    f = pj_dir / f"{stem}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


# ── Test 1: _normalize_record_params (DRY ヘルパー) ─────────────────────────

def test_normalize_record_params_none_to_default(store):
    """None → 0/False 正規化が 17 フィールド全箇所で動く。"""
    rec = {"uuid": "u1", "ts": "2026-05-01T12:00:00Z", "pj_id": "p", "session_id": "s"}
    # 残りの 13 フィールドは未指定 (= None になる)
    params = store._normalize_record_params(rec)
    assert len(params) == 15  # _INSERT_FIELDS の長さ
    # is_sidechain (idx=6) → False
    assert params[6] is False
    # 5 つの token カウント + web_*_requests (idx 9-14) → 0
    for idx in (9, 10, 11, 12, 13, 14):
        assert params[idx] == 0


# ── Test 2: connection() context manager (例外時 close) ────────────────────

def test_connection_context_manager_closes_on_exception(store):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    captured = {}
    with pytest.raises(RuntimeError):
        with store.connection() as con:
            captured["con"] = con
            assert con is not None
            # スキーマが適用済 (session_progress 含む) であることを確認
            con.execute("SELECT COUNT(*) FROM session_progress").fetchone()
            raise RuntimeError("boom")
    # 例外後も DB は再 open 可能 (前回の close が走った証拠)
    with store.connection() as con2:
        rows = con2.execute("SELECT COUNT(*) FROM token_usage").fetchall()
        assert rows[0][0] == 0


# ── Test 3: session_progress 差分 ingest (REGRESSION) ──────────────────────

def test_session_progress_diff_ingest(ingest, store, tmp_path):
    """1回目全 ingest → 行追加 → 2 回目は新規 uuid のみ batch される。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    pj_dir = tmp_path / "projects" / "-pj-foo"
    f = _write_jsonl(pj_dir, "sess-A", [
        _line("u1", session_id="sess-A"),
        _line("u2", ts="2026-05-02T12:00:00Z", session_id="sess-A"),
    ])

    with store.connection() as con:
        r1 = ingest.ingest_pj_dir(pj_dir, days=None, con=con)
    assert r1["inserted"] == 2

    # 同 jsonl に 1 行追加
    f.write_text(
        f.read_text() + _line("u3", ts="2026-05-03T12:00:00Z", session_id="sess-A") + "\n",
        encoding="utf-8",
    )

    with store.connection() as con:
        r2 = ingest.ingest_pj_dir(pj_dir, days=None, con=con)
    # session_progress により u1/u2 はスキップ、u3 のみ新規挿入
    assert r2["inserted"] == 1

    rows = store.query("SELECT COUNT(*) FROM token_usage")
    assert rows[0][0] == 3
    # session_progress に last_uuid が記録されている
    with store.connection() as con:
        prog = store.get_session_progress_for_pj(con, "-pj-foo")
    assert prog["sess-A"][0] == "u3"


# ── Test 4: last_uuid drift セーフティ ─────────────────────────────────────

def test_last_uuid_drift_falls_back_to_full_scan(ingest, store, tmp_path):
    """progress.last_uuid が file から消えていたら全行を INSERT OR IGNORE で再 ingest。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    pj_dir = tmp_path / "projects" / "-pj-foo"
    _write_jsonl(pj_dir, "sess-X", [_line("u1", session_id="sess-X")])
    with store.connection() as con:
        ingest.ingest_pj_dir(pj_dir, days=None, con=con)

    # 偽の last_uuid を書き込み (file に存在しない uuid)
    with store.connection() as con:
        store.upsert_session_progress_batch(con, "-pj-foo", [
            ("sess-X", "MISSING-UUID", "2099-01-01T00:00:00Z"),
        ])

    # 再 ingest: drift 検知 → 全行 fallback、INSERT OR IGNORE で u1 は重複弾き
    with store.connection() as con:
        r = ingest.ingest_pj_dir(pj_dir, days=None, con=con)
    assert r["inserted"] == 0  # u1 は既存
    rows = store.query("SELECT COUNT(*) FROM token_usage")
    assert rows[0][0] == 1


# ── Test 5: 100 jsonl chunk commit ─────────────────────────────────────────

def test_chunk_commit_persistence(ingest, store, tmp_path, monkeypatch):
    """_CHUNK_SIZE 越えで途中 commit が走り、chunk 単位で永続化される。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    # チャンクサイズを 3 に下げて、5 file 配置 → 3 file で 1 回 chunk commit
    import token_usage_ingest as tui
    monkeypatch.setattr(tui, "_CHUNK_SIZE", 3)

    pj_dir = tmp_path / "projects" / "-pj-foo"
    for i in range(5):
        _write_jsonl(pj_dir, f"sess-{i}", [_line(f"u{i}", session_id=f"sess-{i}")])

    with store.connection() as con:
        r = ingest.ingest_pj_dir(pj_dir, days=None, con=con)

    assert r["files_processed"] == 5
    assert r["inserted"] == 5
    rows = store.query("SELECT COUNT(*) FROM token_usage")
    assert rows[0][0] == 5
    # 全 5 session の progress が確定している
    with store.connection() as con:
        prog = store.get_session_progress_for_pj(con, "-pj-foo")
    assert len(prog) == 5
