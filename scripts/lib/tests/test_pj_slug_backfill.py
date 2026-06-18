"""pj_slug_backfill のテスト（#593 バックフィル）。

書込側正規化（#593 write-side）以前に書かれた既存レコードの project / project_path に
混入した幻PJ slug（worktree フルパス / basename ばらつき）を worktree 安全 slug へ
回収正規化する。対象3ストア: corrections.jsonl（project_path）/ subagents.jsonl（project）/
sessions.db（project 列 + raw_json 内 project、DuckDB UPDATE）。

dry-run 既定（apply=False は書込ゼロ）・冪等（再実行で無変化）。実 DATA_DIR は触らず
fixture dir のみで検証する。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import pj_slug_backfill as bf  # noqa: E402
import session_store  # noqa: E402

requires_duckdb = pytest.mark.skipif(
    not session_store.HAS_DUCKDB, reason="duckdb が無い環境"
)

# worktree フルパス → 親 repo slug amamo / 通常フルパス → basename / basename 素通し。
_WT = "/Users/x/tools/amamo/.claude/worktrees/evolve"
_FULL = "/Users/x/rl-anything"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ============================================================================
# corrections.jsonl（project_path）
# ============================================================================

class TestCorrectionsBackfill:
    def test_dry_run_writes_nothing(self, tmp_path):
        corr = tmp_path / "corrections.jsonl"
        _write_jsonl(corr, [
            {"project_path": _WT, "correction_type": "iya", "session_id": "s1"},
            {"project_path": _FULL, "correction_type": "no", "session_id": "s2"},
        ])
        before = corr.read_bytes()
        summary = bf.backfill(tmp_path, apply=False)
        assert corr.read_bytes() == before  # 書込ゼロ
        # 正規化「予定」件数を報告する（フルパス2件）
        assert summary["corrections"]["normalized"] == 2
        assert summary["applied"] is False

    def test_apply_normalizes_project_path(self, tmp_path):
        corr = tmp_path / "corrections.jsonl"
        _write_jsonl(corr, [
            {"project_path": _WT, "correction_type": "iya", "session_id": "s1"},
            {"project_path": _FULL, "correction_type": "no", "session_id": "s2"},
            {"project_path": "amamo", "correction_type": "stop", "session_id": "s3"},  # 既に slug
            {"correction_type": "x", "session_id": "s4"},  # project_path 無し
        ])
        summary = bf.backfill(tmp_path, apply=True)
        recs = _read_jsonl(corr)
        assert recs[0]["project_path"] == "amamo"  # worktree → 親 repo
        assert recs[1]["project_path"] == "rl-anything"  # フルパス → basename
        assert recs[2]["project_path"] == "amamo"  # 既に slug は不変
        assert "project_path" not in recs[3] or recs[3].get("project_path") is None
        # 既に slug の1件と project_path 無しの1件は normalized にカウントしない
        assert summary["corrections"]["normalized"] == 2
        # 他フィールドは保全
        assert recs[0]["correction_type"] == "iya"
        assert recs[0]["session_id"] == "s1"

    def test_idempotent(self, tmp_path):
        corr = tmp_path / "corrections.jsonl"
        _write_jsonl(corr, [
            {"project_path": _WT, "correction_type": "iya", "session_id": "s1"},
        ])
        bf.backfill(tmp_path, apply=True)
        after_first = corr.read_bytes()
        summary2 = bf.backfill(tmp_path, apply=True)
        assert corr.read_bytes() == after_first  # 2回目は無変化
        assert summary2["corrections"]["normalized"] == 0

    def test_no_file_is_noop(self, tmp_path):
        summary = bf.backfill(tmp_path, apply=True)
        assert summary["corrections"]["normalized"] == 0


# ============================================================================
# subagents.jsonl（project）
# ============================================================================

class TestSubagentsBackfill:
    def test_apply_normalizes_project(self, tmp_path):
        sub = tmp_path / "subagents.jsonl"
        _write_jsonl(sub, [
            {"project": _WT, "agent_type": "Explore", "session_id": "s1"},
            {"project": "feedback", "agent_type": "Plan", "session_id": "s2"},  # basename 素通し
        ])
        summary = bf.backfill(tmp_path, apply=True)
        recs = _read_jsonl(sub)
        assert recs[0]["project"] == "amamo"
        assert recs[1]["project"] == "feedback"  # basename は変わらない（情報欠落で復元不能）
        assert summary["subagents"]["normalized"] == 1

    def test_dry_run_writes_nothing(self, tmp_path):
        sub = tmp_path / "subagents.jsonl"
        _write_jsonl(sub, [{"project": _WT, "agent_type": "Explore", "session_id": "s1"}])
        before = sub.read_bytes()
        bf.backfill(tmp_path, apply=False)
        assert sub.read_bytes() == before

    def test_idempotent(self, tmp_path):
        sub = tmp_path / "subagents.jsonl"
        _write_jsonl(sub, [{"project": _WT, "agent_type": "Explore", "session_id": "s1"}])
        bf.backfill(tmp_path, apply=True)
        after = sub.read_bytes()
        summary2 = bf.backfill(tmp_path, apply=True)
        assert sub.read_bytes() == after
        assert summary2["subagents"]["normalized"] == 0


# ============================================================================
# sessions.db（project 列 + raw_json 内 project、DuckDB UPDATE）
# ============================================================================

def _seed_sessions_db(data_dir: Path, records: list[dict]) -> None:
    """records を sessions.jsonl に書いて session_store.ingest() で db へ取り込む。"""
    old_dir = session_store.DATA_DIR
    old_db = session_store.SESSIONS_DB
    old_jsonl = session_store.SESSIONS_JSONL
    try:
        session_store.DATA_DIR = data_dir
        session_store.SESSIONS_DB = data_dir / "sessions.db"
        session_store.SESSIONS_JSONL = data_dir / "sessions.jsonl"
        path = data_dir / "sessions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        session_store.ingest()
    finally:
        session_store.DATA_DIR = old_dir
        session_store.SESSIONS_DB = old_db
        session_store.SESSIONS_JSONL = old_jsonl


def _read_db_projects(db_path: Path):
    """sessions.db の (project 列, raw_json 内 project) を session_id 別に返す。"""
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute("SELECT session_id, project, raw_json FROM sessions").fetchall()
    finally:
        con.close()
    out = {}
    for sid, proj, raw in rows:
        out[sid] = (proj, json.loads(raw).get("project"))
    return out


class TestSessionsDbBackfill:
    @requires_duckdb
    def test_apply_normalizes_column_and_raw_json(self, tmp_path):
        _seed_sessions_db(tmp_path, [
            {"session_id": "s1", "timestamp": "2026-06-10T00:00:00+00:00",
             "project": _WT, "error_count": 0},
            {"session_id": "s2", "timestamp": "2026-06-10T00:01:00+00:00",
             "project": "feedback", "error_count": 0},  # basename 素通し
        ])
        summary = bf.backfill(tmp_path, apply=True)
        projs = _read_db_projects(tmp_path / "sessions.db")
        # worktree フルパスが column / raw_json 両方で本体 repo slug に正規化される。
        assert projs["s1"] == ("amamo", "amamo")
        assert projs["s2"] == ("feedback", "feedback")  # basename 不変
        assert summary["sessions_db"]["normalized"] == 1

    @requires_duckdb
    def test_dry_run_writes_nothing(self, tmp_path):
        _seed_sessions_db(tmp_path, [
            {"session_id": "s1", "timestamp": "2026-06-10T00:00:00+00:00",
             "project": _WT, "error_count": 0},
        ])
        before = (tmp_path / "sessions.db").read_bytes()
        summary = bf.backfill(tmp_path, apply=False)
        assert (tmp_path / "sessions.db").read_bytes() == before
        assert summary["sessions_db"]["normalized"] == 1  # 予定件数は数える

    @requires_duckdb
    def test_idempotent(self, tmp_path):
        _seed_sessions_db(tmp_path, [
            {"session_id": "s1", "timestamp": "2026-06-10T00:00:00+00:00",
             "project": _WT, "error_count": 0},
        ])
        bf.backfill(tmp_path, apply=True)
        projs1 = _read_db_projects(tmp_path / "sessions.db")
        summary2 = bf.backfill(tmp_path, apply=True)
        projs2 = _read_db_projects(tmp_path / "sessions.db")
        assert projs1 == projs2
        assert summary2["sessions_db"]["normalized"] == 0

    @requires_duckdb
    def test_no_db_is_noop(self, tmp_path):
        summary = bf.backfill(tmp_path, apply=True)
        assert summary["sessions_db"]["normalized"] == 0


# ============================================================================
# 安全性: 実 DATA_DIR を触らない / format_summary
# ============================================================================

class TestSummaryFormat:
    def test_format_summary_human_readable(self, tmp_path):
        corr = tmp_path / "corrections.jsonl"
        _write_jsonl(corr, [{"project_path": _WT, "session_id": "s1"}])
        summary = bf.backfill(tmp_path, apply=False)
        text = bf.format_summary(summary)
        assert "corrections" in text
        # dry-run は「予定」表現を含む
        assert "予定" in text or "dry-run" in text.lower()
