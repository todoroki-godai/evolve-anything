#!/usr/bin/env python3
"""token_usage_ingest — transcript パース + walker テスト。"""
from __future__ import annotations

import json
import os
import sys
import time
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
    # ingest が _store として保持している参照も差し替える
    monkeypatch.setattr(tui, "_store", store)
    return tui


def _line(
    uuid="u1",
    session_id="sess-1",
    ts="2026-05-01T12:00:00Z",
    role="assistant",
    model="claude-sonnet-4-7",
    is_sidechain=False,
    usage=None,
    omit_usage=False,
    omit_uuid=False,
):
    msg = {"role": role}
    if role == "assistant" and model is not None:
        msg["model"] = model
    if not omit_usage:
        msg["usage"] = usage if usage is not None else {
            "input_tokens": 3,
            "cache_creation_input_tokens": 24201,
            "cache_read_input_tokens": 15193,
            "output_tokens": 564,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        }
    obj = {
        "sessionId": session_id,
        "parentUuid": None,
        "isSidechain": is_sidechain,
        "timestamp": ts,
        "message": msg,
    }
    if not omit_uuid:
        obj["uuid"] = uuid
    return json.dumps(obj)


def test_parse_happy_path(ingest):
    rec = ingest.parse_transcript_line(_line(), pj_id="-pj-foo", pj_slug="foo")
    assert rec is not None
    assert rec["uuid"] == "u1"
    assert rec["ts"] == "2026-05-01T12:00:00Z"
    assert rec["pj_id"] == "-pj-foo"
    assert rec["session_id"] == "sess-1"
    assert rec["input_tokens"] == 3
    assert rec["cache_creation_input_tokens"] == 24201
    assert rec["cache_read_input_tokens"] == 15193
    assert rec["output_tokens"] == 564
    assert rec["model"] == "claude-sonnet-4-7"
    assert rec["role"] == "assistant"
    assert rec["is_sidechain"] is False
    assert rec["web_search_requests"] == 0


def test_parse_missing_usage(ingest):
    assert ingest.parse_transcript_line(_line(omit_usage=True)) is None


def test_parse_missing_uuid(ingest):
    assert ingest.parse_transcript_line(_line(omit_uuid=True)) is None


def test_parse_malformed_json(ingest):
    assert ingest.parse_transcript_line("{not json") is None
    assert ingest.parse_transcript_line("") is None


def test_parse_sidechain_flag(ingest):
    rec = ingest.parse_transcript_line(_line(is_sidechain=True))
    assert rec is not None
    assert rec["is_sidechain"] is True


def test_pj_slug_extraction(ingest):
    assert ingest._pj_slug_from_id(
        "-Users-todoroki-tools-rl-anything"
    ) == "anything"
    # designではDIR末尾セグメントだが、`-`splitの最後は実際は"anything"
    # フォールバック: 空文字列なら入力を返す
    assert ingest._pj_slug_from_id("rl-anything") == "anything"
    assert ingest._pj_slug_from_id("") == ""


def _write_pj(tmp_path, pj_name, lines, mtime=None):
    pj_dir = tmp_path / "projects" / pj_name
    pj_dir.mkdir(parents=True, exist_ok=True)
    f = pj_dir / "transcript.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return pj_dir


def test_ingest_pj_dir_initial(ingest, tmp_path, store):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    pj_dir = _write_pj(tmp_path, "-pj-foo", [
        _line(uuid="u1"),
        _line(uuid="u2", ts="2026-05-02T12:00:00Z"),
        _line(omit_usage=True),  # skipped
    ])
    res = ingest.ingest_pj_dir(pj_dir, days=None)
    assert res["inserted"] == 2
    assert res["skipped"] == 1
    assert res["files_processed"] == 1


def test_ingest_pj_dir_idempotent(ingest, tmp_path, store):
    """同一ファイル 2 回 ingest で行数不変 (CRITICAL)。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    pj_dir = _write_pj(tmp_path, "-pj-foo", [
        _line(uuid="u1"),
        _line(uuid="u2", ts="2026-05-02T12:00:00Z"),
    ])
    r1 = ingest.ingest_pj_dir(pj_dir, days=None)
    r2 = ingest.ingest_pj_dir(pj_dir, days=None)
    assert r1["inserted"] == 2
    assert r2["inserted"] == 0
    rows = store.query("SELECT COUNT(*) FROM token_usage")
    assert rows[0][0] == 2


def test_ingest_pj_dir_days_filter(ingest, tmp_path, store):
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    old_mtime = time.time() - 200 * 86400
    pj_dir = _write_pj(tmp_path, "-pj-foo", [_line(uuid="u-old")], mtime=old_mtime)
    res = ingest.ingest_pj_dir(pj_dir, days=90)
    assert res["files_processed"] == 0
    assert res["inserted"] == 0
    # days=None なら処理される
    res2 = ingest.ingest_pj_dir(pj_dir, days=None)
    assert res2["files_processed"] == 1
    assert res2["inserted"] == 1
