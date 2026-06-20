"""session_store.read_session_records の union read テスト（#469）。

outcome 系（metrics / promotion_readiness）の session 系分母を実効化するため、
sessions.jsonl 直読でなく DuckDB sessions.db + 未 ingest live jsonl の union read に
切り替えた。本テストはその union read 関数を直接検証する:

  - db のみ / jsonl のみ / 両方+重複あり（dedup）/ duckdb 無し fallback
  - 読み取り専用（db を新規作成しない・1バイトも書かない）

決定論・LLM 非依存。monkeypatch は import した module を直接 patch する（pitfall 準拠）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import session_store  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


def _session(sid: str, ts: str, **extra) -> dict:
    rec = {"session_id": sid, "timestamp": ts, "project": "/p/a"}
    rec.update(extra)
    return rec


def _ingest_into_db(data_dir: Path, records: list[dict]) -> None:
    """records を sessions.jsonl に書いて ingest()（DATA_DIR を data_dir へ向ける）。"""
    import duckdb  # noqa: F401 -- ある前提でしか呼ばない

    # ingest はモジュール定数を読むので、その場限りで差し替える。
    old_dir = session_store.DATA_DIR
    old_db = session_store.SESSIONS_DB
    old_jsonl = session_store.SESSIONS_JSONL
    try:
        session_store.DATA_DIR = data_dir
        session_store.SESSIONS_DB = data_dir / "sessions.db"
        session_store.SESSIONS_JSONL = data_dir / "sessions.jsonl"
        _write_jsonl(session_store.SESSIONS_JSONL, records)
        session_store.ingest()  # jsonl→db に取り込み + jsonl を rotate
    finally:
        session_store.DATA_DIR = old_dir
        session_store.SESSIONS_DB = old_db
        session_store.SESSIONS_JSONL = old_jsonl


requires_duckdb = pytest.mark.skipif(
    not session_store.HAS_DUCKDB, reason="duckdb が無い環境"
)


class TestReadSessionRecords:
    @requires_duckdb
    def test_db_only(self, tmp_path):
        """ingest 済み（jsonl は rotate 消失）でも db から読める。"""
        _ingest_into_db(
            tmp_path,
            [
                _session("s1", "2026-06-01T00:00:00+00:00", error_count=0),
                _session("s2", "2026-06-02T00:00:00+00:00", error_count=1),
            ],
        )
        # ingest 後 live jsonl は rotate されているはず。
        assert not (tmp_path / "sessions.jsonl").exists()

        recs = session_store.read_session_records(tmp_path)
        sids = sorted(r["session_id"] for r in recs)
        assert sids == ["s1", "s2"]

    def test_jsonl_only(self, tmp_path):
        """db が無くても live jsonl から読める（fallback）。"""
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _session("s1", "2026-06-01T00:00:00+00:00"),
                _session("s2", "2026-06-02T00:00:00+00:00"),
            ],
        )
        assert not (tmp_path / "sessions.db").exists()
        recs = session_store.read_session_records(tmp_path)
        assert sorted(r["session_id"] for r in recs) == ["s1", "s2"]

    @requires_duckdb
    def test_db_plus_uningested_jsonl_dedups(self, tmp_path):
        """db に取り込み済み + 同一レコードが live jsonl にも残るケースで二重カウントしない。"""
        rec_a = _session("s1", "2026-06-01T00:00:00+00:00")
        rec_b = _session("s2", "2026-06-02T00:00:00+00:00")
        _ingest_into_db(tmp_path, [rec_a, rec_b])
        # ingest 後に「未 ingest として s2(重複) + s3(新規)」が live jsonl に出現したとする。
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [rec_b, _session("s3", "2026-06-03T00:00:00+00:00")],
        )
        recs = session_store.read_session_records(tmp_path)
        sids = sorted(r["session_id"] for r in recs)
        # s2 は (session_id, timestamp) で dedup → 1 回のみ。
        assert sids == ["s1", "s2", "s3"]

    @requires_duckdb
    def test_db_wins_on_duplicate_key(self, tmp_path):
        """同一 (session_id, timestamp) は db レコードを優先する。"""
        _ingest_into_db(tmp_path, [_session("s1", "2026-06-01T00:00:00+00:00", error_count=0)])
        # jsonl 側は同キーで error_count=9（衝突）。
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [_session("s1", "2026-06-01T00:00:00+00:00", error_count=9)],
        )
        recs = session_store.read_session_records(tmp_path)
        assert len(recs) == 1
        assert recs[0]["error_count"] == 0  # db 優先

    def test_duckdb_missing_falls_back_to_jsonl(self, tmp_path, monkeypatch):
        """HAS_DUCKDB=False のとき db を無視し jsonl のみ読む。"""
        monkeypatch.setattr(session_store, "HAS_DUCKDB", False)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [_session("s1", "2026-06-01T00:00:00+00:00")],
        )
        recs = session_store.read_session_records(tmp_path)
        assert [r["session_id"] for r in recs] == ["s1"]

    def test_since_filters_jsonl(self, tmp_path):
        """since 指定で timestamp <= since を除外する（jsonl 経路）。"""
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _session("old", "2026-05-01T00:00:00+00:00"),
                _session("new", "2026-06-10T00:00:00+00:00"),
            ],
        )
        recs = session_store.read_session_records(
            tmp_path, since="2026-06-01T00:00:00+00:00"
        )
        assert [r["session_id"] for r in recs] == ["new"]

    def test_empty_returns_empty(self, tmp_path):
        assert session_store.read_session_records(tmp_path) == []

    @requires_duckdb
    def test_read_only_does_not_create_db(self, tmp_path):
        """db も jsonl も無い dir を read しても sessions.db を新規作成しない（read-only 契約）。"""
        before = sorted(p.name for p in tmp_path.iterdir())
        session_store.read_session_records(tmp_path)
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after  # 1 ファイルも増やさない

    @requires_duckdb
    def test_read_only_does_not_mutate_db_bytes(self, tmp_path):
        """既存 db を read しても db のバイトを変えない（dry-run byte 契約 #461 維持）。"""
        _ingest_into_db(tmp_path, [_session("s1", "2026-06-01T00:00:00+00:00")])
        db_path = tmp_path / "sessions.db"
        before = db_path.read_bytes()
        session_store.read_session_records(tmp_path)
        after = db_path.read_bytes()
        assert before == after


class TestReadSessionRecordsUnion:
    """read_session_records_union: canonical + legacy/plugins-data の cross-dir union（#45）。

    iter_read_data_dirs が canonical.parent から候補を導出するため、canonical を
    ``tmp/evolve-anything`` にすると兄弟 dir（``tmp/rl-anything`` 等）を作るだけで
    cross-dir union を hermetic に検証できる（実 home を読まない）。
    """

    @staticmethod
    def _canonical(root: Path) -> Path:
        c = root / "evolve-anything"
        c.mkdir(parents=True, exist_ok=True)
        return c

    def test_unions_across_canonical_and_legacy(self, tmp_path):
        """canonical の s1 と legacy 兄弟 dir の s2 を 1 つに union する。"""
        canonical = self._canonical(tmp_path)
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(canonical / "sessions.jsonl", [_session("s1", "2026-06-01T00:00:00+00:00")])
        _write_jsonl(legacy / "sessions.jsonl", [_session("s2", "2026-06-02T00:00:00+00:00")])

        recs = session_store.read_session_records_union(canonical)
        assert sorted(r["session_id"] for r in recs) == ["s1", "s2"]

    def test_canonical_wins_on_duplicate_key(self, tmp_path):
        """同一 (session_id, timestamp) が複数 dir にあるとき canonical を優先する。"""
        canonical = self._canonical(tmp_path)
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(
            canonical / "sessions.jsonl",
            [_session("s1", "2026-06-01T00:00:00+00:00", error_count=0)],
        )
        _write_jsonl(
            legacy / "sessions.jsonl",
            [_session("s1", "2026-06-01T00:00:00+00:00", error_count=9)],
        )
        recs = session_store.read_session_records_union(canonical)
        assert len(recs) == 1
        assert recs[0]["error_count"] == 0  # canonical 優先

    def test_hermetic_tmp_only_reads_canonical(self, tmp_path):
        """兄弟 dir を作らなければ canonical のみ（実 home の legacy を読まない）。"""
        canonical = self._canonical(tmp_path)
        _write_jsonl(canonical / "sessions.jsonl", [_session("s1", "2026-06-01T00:00:00+00:00")])
        recs = session_store.read_session_records_union(canonical)
        assert [r["session_id"] for r in recs] == ["s1"]

    def test_since_propagates_to_each_dir(self, tmp_path):
        """since は各候補 dir の read に伝播する。"""
        canonical = self._canonical(tmp_path)
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(
            canonical / "sessions.jsonl",
            [
                _session("old", "2026-05-01T00:00:00+00:00"),
                _session("new-c", "2026-06-10T00:00:00+00:00"),
            ],
        )
        _write_jsonl(
            legacy / "sessions.jsonl",
            [
                _session("old-l", "2026-05-02T00:00:00+00:00"),
                _session("new-l", "2026-06-11T00:00:00+00:00"),
            ],
        )
        recs = session_store.read_session_records_union(
            canonical, since="2026-06-01T00:00:00+00:00"
        )
        assert sorted(r["session_id"] for r in recs) == ["new-c", "new-l"]

    def test_empty_when_no_data(self, tmp_path):
        canonical = self._canonical(tmp_path)
        assert session_store.read_session_records_union(canonical) == []
