"""store_schema_repair のテスト（#156）。

CTAS で制約を落としたテーブル + キー重複データを合成 fixture DB に作り、
- 制約欠落を検出して自動修復すること（PK / UNIQUE index 復元）
- 修復後に INSERT OR IGNORE / ON CONFLICT DO NOTHING が成功し冪等になること
- dedup がキーグループ順（物理 PK → 論理 UNIQUE）で決定論に行われること
- 健全なテーブルは no-op（False）で触らないこと
を検証する。書き込み先は tmp_path のみ（実 DATA_DIR に触れない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib = Path(__file__).resolve().parent.parent
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

duckdb = pytest.importorskip("duckdb")

import store_schema_repair as ssr  # noqa: E402


# ── canonical schema（テスト内では store の実 _SCHEMA_SQL を import して単一ソース確認）──
import token_usage_store as tus  # noqa: E402
from utterance_archive import store as ustore  # noqa: E402


def _damaged_token_usage_db(path: Path, rows: list) -> None:
    """CTAS 相当で制約なしの token_usage テーブルを作る（constraints=[]）。"""
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE token_usage ("
        "uuid VARCHAR, ts TIMESTAMP, pj_id VARCHAR, pj_slug VARCHAR, session_id VARCHAR,"
        "parent_uuid VARCHAR, is_sidechain BOOLEAN, model VARCHAR, role VARCHAR,"
        "input_tokens INTEGER, output_tokens INTEGER, cache_creation_input_tokens INTEGER,"
        "cache_read_input_tokens INTEGER, web_search_requests INTEGER,"
        "web_fetch_requests INTEGER, ingested_at TIMESTAMP)"
    )
    con.executemany(
        "INSERT INTO token_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.close()


def _base_row(uuid: str, out: int = 0):
    return [
        uuid, "2026-06-01 00:00:00", "pj", "slug", "sess",
        None, False, "opus", "assistant",
        1, out, 0, 0, 0, 0, "2026-06-01 00:00:00",
    ]


def test_detects_missing_primary_key(tmp_path):
    db = tmp_path / "token_usage.db"
    _damaged_token_usage_db(db, [_base_row("a")])
    con = duckdb.connect(str(db))
    try:
        assert ssr.needs_repair(con, "token_usage") is True
    finally:
        con.close()


def test_healthy_table_is_noop(tmp_path):
    """PK 付きで作った健全テーブルは needs_repair=False, repair_table=False。"""
    db = tmp_path / "token_usage.db"
    con = duckdb.connect(str(db))
    con.execute(tus._SCHEMA_SQL)
    try:
        assert ssr.needs_repair(con, "token_usage") is False
        assert ssr.repair_table(con, "token_usage", tus._SCHEMA_SQL, [("uuid",)]) is False
    finally:
        con.close()


def test_repair_token_usage_dedups_and_restores_pk(tmp_path):
    db = tmp_path / "token_usage.db"
    # uuid 'a' が 3 行（重複）、'b' が 1 行
    _damaged_token_usage_db(
        db, [_base_row("a", 1), _base_row("a", 2), _base_row("a", 3), _base_row("b", 9)]
    )
    con = duckdb.connect(str(db))
    try:
        assert ssr.repair_table(con, "token_usage", tus._SCHEMA_SQL, [("uuid",)]) is True
        # PK 復元
        pk = con.execute(
            "SELECT COUNT(*) FROM duckdb_constraints() "
            "WHERE table_name='token_usage' AND constraint_type='PRIMARY KEY'"
        ).fetchone()[0]
        assert pk == 1
        # uuid 単位 dedup: a=1行, b=1行
        rows = con.execute("SELECT uuid FROM token_usage ORDER BY uuid").fetchall()
        assert [r[0] for r in rows] == ["a", "b"]
        # 復元後は INSERT OR IGNORE が動作し冪等
        con.execute(tus._INSERT_SQL, tus._normalize_record_params({"uuid": "a"}))
        assert con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == 2
    finally:
        con.close()


def test_repair_token_usage_dedup_is_deterministic(tmp_path):
    """全列 ORDER BY で決定論に 1 行を残す（同 fixture で 2 回実行し同一結果）。"""
    results = []
    for i in range(2):
        db = tmp_path / f"t{i}.db"
        _damaged_token_usage_db(
            db, [_base_row("a", 3), _base_row("a", 1), _base_row("a", 2)]
        )
        con = duckdb.connect(str(db))
        ssr.repair_table(con, "token_usage", tus._SCHEMA_SQL, [("uuid",)])
        kept = con.execute("SELECT output_tokens FROM token_usage").fetchall()
        con.close()
        results.append(kept)
    assert results[0] == results[1]
    # 全列 ASC ORDER なので最小 output_tokens (=1) が残る
    assert results[0][0][0] == 1


def _damaged_utterances_db(path: Path, rows: list) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE utterances ("
        "source_path TEXT, line_no INTEGER, pj_slug TEXT, session_id TEXT,"
        "timestamp TEXT, text TEXT, text_hash TEXT, prev_action TEXT,"
        "source_kind TEXT, extractor_version INTEGER, ingested_at TEXT)"
    )
    con.executemany(
        "INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    con.close()


def _utt_row(sp, ln, sid, ts, text, th):
    return [sp, ln, "pj", sid, ts, text, th, None, "dialogue", 1, "2026-06-01T00:00:00Z"]


def test_utterances_needs_repair_when_unique_index_missing(tmp_path):
    db = tmp_path / "utterances.db"
    _damaged_utterances_db(db, [_utt_row("f", 1, "s", "t1", "hi", "h1")])
    con = duckdb.connect(str(db))
    try:
        assert ssr.needs_repair(con, "utterances", require_unique_index=True) is True
    finally:
        con.close()


def test_repair_utterances_two_stage_dedup_and_conflict(tmp_path):
    db = tmp_path / "utterances.db"
    _damaged_utterances_db(
        db,
        [
            _utt_row("f", 1, "s", "t1", "hi", "h1"),
            _utt_row("f", 1, "s", "t1", "hi", "h1"),   # 物理 PK 重複
            _utt_row("f", 2, "s", "t1", "bye", "h1"),  # 論理 UNIQUE 重複 (s,t1,h1)
            _utt_row("g", 3, "s2", "t2", "yo", "h2"),
        ],
    )
    con = duckdb.connect(str(db))
    try:
        ok = ssr.repair_table(
            con, "utterances", ustore._SCHEMA_SQL,
            [("source_path", "line_no"), ("session_id", "timestamp", "text_hash")],
            require_unique_index=True,
        )
        assert ok is True
        # PK + UNIQUE index 復元
        assert con.execute(
            "SELECT COUNT(*) FROM duckdb_constraints() "
            "WHERE table_name='utterances' AND constraint_type='PRIMARY KEY'"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM duckdb_indexes() "
            "WHERE table_name='utterances' AND is_unique=TRUE"
        ).fetchone()[0] >= 1
        # 物理 PK → 論理 UNIQUE の順 dedup で 2 行残る
        rows = con.execute(
            "SELECT source_path, line_no FROM utterances ORDER BY source_path, line_no"
        ).fetchall()
        assert rows == [("f", 1), ("g", 3)]
        # ON CONFLICT DO NOTHING が復元され冪等
        con.execute(ustore._INSERT_SQL, _utt_row("f", 1, "s", "t1", "hi", "h1"))
        assert con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 2
    finally:
        con.close()


def test_utterance_connection_self_heals_on_write_path(tmp_path):
    """ustore.connection()（write 経路・repair 既定 True）が破損 DB を開いた時点で修復する。"""
    db = tmp_path / "utterances.db"
    _damaged_utterances_db(
        db,
        [
            _utt_row("f", 1, "s", "t1", "hi", "h1"),
            _utt_row("f", 1, "s", "t1", "hi", "h1"),
            _utt_row("g", 3, "s2", "t2", "yo", "h2"),
        ],
    )
    with ustore.connection(db) as con:
        # 修復後 PK が存在し件数は dedup 済み
        assert con.execute(
            "SELECT COUNT(*) FROM duckdb_constraints() "
            "WHERE table_name='utterances' AND constraint_type='PRIMARY KEY'"
        ).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 2


def test_utterance_read_path_does_not_repair(tmp_path):
    """repair=False（read 経路）は破損 DB を修復しない（読み取りで書き込みを発火させない）。"""
    db = tmp_path / "utterances.db"
    _damaged_utterances_db(db, [_utt_row("f", 1, "s", "t1", "hi", "h1")])
    with ustore.connection(db, repair=False) as con:
        # PK は復元されないまま（read はスキーマを触らない）
        assert con.execute(
            "SELECT COUNT(*) FROM duckdb_constraints() "
            "WHERE table_name='utterances' AND constraint_type='PRIMARY KEY'"
        ).fetchone()[0] == 0


def test_token_usage_connect_self_heals(tmp_path, monkeypatch):
    """token_usage_store._connect() が破損 token_usage.db を開いた時点で修復する。"""
    db = tmp_path / "token_usage.db"
    _damaged_token_usage_db(db, [_base_row("a", 1), _base_row("a", 2), _base_row("b", 5)])
    monkeypatch.setattr(tus, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tus, "USAGE_DB", db)
    con = tus._connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM duckdb_constraints() "
            "WHERE table_name='token_usage' AND constraint_type='PRIMARY KEY'"
        ).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == 2
    finally:
        con.close()


def test_repair_known_tables_selects_present(tmp_path):
    """repair_known_tables は接続内の既知テーブルのみ修復する。"""
    db = tmp_path / "token_usage.db"
    _damaged_token_usage_db(db, [_base_row("a"), _base_row("a")])
    con = duckdb.connect(str(db))
    try:
        present = {r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables()"
        ).fetchall()}
        repaired = ssr.repair_known_tables(con, present_tables=present)
        assert "token_usage" in repaired
        assert con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == 1
    finally:
        con.close()
