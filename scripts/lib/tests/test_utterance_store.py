"""utterance_archive.store のテスト（#430）。

DuckDB スキーマ・物理 PK・論理 UNIQUE・staleness marker・ingest_state を検証する。
書き込み先は tmp_path のみ（実 DATA_DIR に触れない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from utterance_archive import store as ustore  # noqa: E402
from utterance_archive.extractor import Utterance  # noqa: E402

pytestmark = pytest.mark.skipif(not ustore.HAS_DUCKDB, reason="DuckDB 未インストール")


def _utt(source_path="/p/s1.jsonl", line_no=1, sid="s1", ts="2026-06-01T00:00:00Z",
         text="hello", text_hash="h1", pj="x", kind="dialogue", prev=None):
    return Utterance(
        source_path=source_path, line_no=line_no, pj_slug=pj, session_id=sid,
        timestamp=ts, text=text, text_hash=text_hash, prev_action=prev,
        source_kind=kind, extractor_version=1,
    )


def test_connect_creates_schema(tmp_path: Path) -> None:
    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert "utterances" in tables
    assert "ingest_state" in tables


def test_insert_and_count(tmp_path: Path) -> None:
    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        n = ustore.insert_utterances(con, [_utt(text="a", text_hash="ha"),
                                            _utt(line_no=2, text="b", text_hash="hb")])
        assert n == 2
        total = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
        assert total == 2


def test_physical_pk_idempotent(tmp_path: Path) -> None:
    """同 (source_path, line_no) の再 INSERT は冪等（重複ゼロ）。"""
    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, [_utt()])
        n2 = ustore.insert_utterances(con, [_utt()])  # 同一物理キー
        assert n2 == 0
        assert con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 1


def test_logical_unique_blocks_resume_dup(tmp_path: Path) -> None:
    """resume 複製: 別ファイル/行でも (session_id, timestamp, text_hash) 一致は弾く。"""
    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, [_utt(source_path="/a/s1.jsonl", line_no=5)])
        # 別ファイル・別行だが同 session_id/timestamp/text_hash（履歴 replay 複製）
        n2 = ustore.insert_utterances(con, [_utt(source_path="/b/s1.jsonl", line_no=99)])
        assert n2 == 0
        assert con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 1


def test_ingest_state_upsert(tmp_path: Path) -> None:
    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        ustore.upsert_ingest_state(con, "/p/s1.jsonl", mtime=100.0, line_offset=10)
        st = ustore.get_ingest_state(con)
        assert st["/p/s1.jsonl"] == (100.0, 10)
        # 更新
        ustore.upsert_ingest_state(con, "/p/s1.jsonl", mtime=200.0, line_offset=25)
        st = ustore.get_ingest_state(con)
        assert st["/p/s1.jsonl"] == (200.0, 25)


# --- staleness marker --------------------------------------------------------

def test_marker_absent_means_stale(tmp_path: Path) -> None:
    """marker 不在 = 未 ingest = stale（∞ 扱い、0日でない）。"""
    assert ustore.read_last_ingest_at(tmp_path) is None
    assert ustore.is_stale(tmp_path, threshold_days=14) is True


def test_marker_written_and_fresh(tmp_path: Path) -> None:
    ustore.write_last_ingest_at(tmp_path)
    ts = ustore.read_last_ingest_at(tmp_path)
    assert ts is not None
    assert ustore.is_stale(tmp_path, threshold_days=14) is False


def test_marker_old_is_stale(tmp_path: Path) -> None:
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    (tmp_path / ustore.MARKER_NAME).write_text(old, encoding="utf-8")
    assert ustore.is_stale(tmp_path, threshold_days=14) is True
