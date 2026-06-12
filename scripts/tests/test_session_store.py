#!/usr/bin/env python3
"""SessionStore の Repository pattern テスト。

DuckDB が SoR、HAS_DUCKDB=False 時は JSONL フォールバック。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = str(_REPO_ROOT / "scripts" / "lib")


def _import_session_store(env: dict[str, str] | None = None) -> dict:
    """subprocess で session_store を import して属性を返す。"""
    code = (
        "import sys, json; "
        f"sys.path.insert(0, {_LIB!r}); "
        "import session_store as ss; "
        "print(json.dumps({"
        "'data_dir': str(ss.DATA_DIR), "
        "'has_duckdb': ss.HAS_DUCKDB, "
        "'sessions_db': str(ss.SESSIONS_DB), "
        "'sessions_jsonl': str(ss.SESSIONS_JSONL)"
        "}))"
    )
    merged = {**os.environ, **(env or {})}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=merged)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """各テスト独立の SessionStore インスタンス。

    monkeypatch.setattr で DATA_DIR / SESSIONS_DB / SESSIONS_JSONL を一時パッチ。
    teardown で自動復元されるため、他テストへ汚染しない。
    """
    sys.path.insert(0, _LIB)
    import session_store
    monkeypatch.setattr(session_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(session_store, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(session_store, "SESSIONS_JSONL", tmp_path / "sessions.jsonl")
    return session_store


class TestPaths:
    def test_default_data_dir(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
        result = _import_session_store()
        assert result["data_dir"] == str(Path.home() / ".claude" / "rl-anything")

    def test_env_override(self, tmp_path):
        result = _import_session_store({"CLAUDE_PLUGIN_DATA": str(tmp_path)})
        assert result["data_dir"] == str(tmp_path)
        assert result["sessions_db"] == str(tmp_path / "sessions.db")
        assert result["sessions_jsonl"] == str(tmp_path / "sessions.jsonl")


class TestAppend:
    """Phase A (#415): append は jsonl 追記のみ。DuckDB 経路は削除済み。

    hot path（hooks）から per-fire connect→INSERT→close を消すのが #415 の根治。
    db への取り込みは batch ingest() のみが行う。
    """

    def test_append_writes_jsonl_only_even_with_duckdb(self, fresh_store, tmp_path):
        """DuckDB があっても append は jsonl にだけ書く（db には触らない）。"""
        record = {
            "session_id": "abc",
            "timestamp": "2026-04-30T12:00:00+00:00",
            "project": "test-project",
            "skill_count": 5,
            "error_count": 0,
        }
        fresh_store.append(record)

        jsonl = tmp_path / "sessions.jsonl"
        assert jsonl.exists(), "append は jsonl に書く"
        lines = jsonl.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["session_id"] == "abc"
        # db は append では作られない（ingest だけが作る）
        assert not (tmp_path / "sessions.db").exists(), "append は db を作らない"

    def test_append_falls_back_to_jsonl_without_duckdb(self, fresh_store, tmp_path, monkeypatch):
        monkeypatch.setattr(fresh_store, "HAS_DUCKDB", False)
        record = {
            "session_id": "xyz",
            "timestamp": "2026-04-30T12:00:00+00:00",
            "project": "test",
        }
        fresh_store.append(record)
        jsonl = tmp_path / "sessions.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["session_id"] == "xyz"

    def test_append_handles_minimal_record(self, fresh_store):
        """timestamp と session_id は必須だが、その他は任意。"""
        record = {"session_id": "min", "timestamp": "2026-04-30T00:00:00+00:00"}
        fresh_store.append(record)  # should not raise


class TestCountUniqueSince:
    def test_counts_distinct_sessions_after_timestamp(self, fresh_store):
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"},
            {"session_id": "a", "timestamp": "2026-04-30T14:00:00+00:00"},  # 重複
            {"session_id": "c", "timestamp": "2026-04-30T15:00:00+00:00"},
        ]
        for r in records:
            fresh_store.append(r)
        count = fresh_store.count_unique_since("2026-04-30T00:00:00+00:00")
        assert count == 3  # a, b, c の 3 ユニーク

    def test_filters_old_sessions(self, fresh_store):
        records = [
            {"session_id": "old", "timestamp": "2025-12-31T00:00:00+00:00"},
            {"session_id": "new", "timestamp": "2026-04-30T00:00:00+00:00"},
        ]
        for r in records:
            fresh_store.append(r)
        count = fresh_store.count_unique_since("2026-01-01T00:00:00+00:00")
        assert count == 1

    def test_empty_returns_zero(self, fresh_store):
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 0


class TestQuery:
    def test_returns_all_sessions(self, fresh_store):
        for i in range(3):
            fresh_store.append({
                "session_id": f"s{i}",
                "timestamp": f"2026-04-30T{12+i:02d}:00:00+00:00",
            })
        results = fresh_store.query()
        assert len(results) == 3

    def test_since_filter(self, fresh_store):
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"},
        ]
        for r in records:
            fresh_store.append(r)
        results = fresh_store.query(since="2026-04-30T12:30:00+00:00")
        assert len(results) == 1
        assert results[0]["session_id"] == "b"

    def test_limit(self, fresh_store):
        for i in range(10):
            fresh_store.append({
                "session_id": f"s{i}",
                "timestamp": f"2026-04-30T{i:02d}:00:00+00:00",
            })
        results = fresh_store.query(limit=3)
        assert len(results) == 3


class TestMigration:
    def test_migrate_jsonl_to_db_imports_all_records(self, fresh_store, tmp_path):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        jsonl = tmp_path / "sessions.jsonl"
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "project": "p1"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "project": "p2"},
            {"session_id": "c", "timestamp": "2026-04-30T14:00:00+00:00"},
        ]
        jsonl.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        imported = fresh_store.migrate_from_jsonl()
        assert imported == 3
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 3

    def test_migrate_is_idempotent(self, fresh_store, tmp_path):
        """同じデータを 2 回 migrate しても重複しない。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        jsonl = tmp_path / "sessions.jsonl"
        records = [{"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"}]
        jsonl.write_text(json.dumps(records[0]), encoding="utf-8")

        fresh_store.migrate_from_jsonl()
        fresh_store.migrate_from_jsonl()  # idempotent
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 1

    def test_migrate_skips_when_db_already_has_data(self, fresh_store, tmp_path):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")

        # まず DB にデータを入れる（jsonl-first なので append→ingest で db に入れる）
        fresh_store.append({"session_id": "db-only", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()  # ingest が jsonl を rotate するので live は空になる
        # JSONL を新規に用意
        jsonl = tmp_path / "sessions.jsonl"
        jsonl.write_text(json.dumps({"session_id": "jsonl-only", "timestamp": "2026-04-30T13:00:00+00:00"}), encoding="utf-8")

        # 自動マイグレーションは sessions.db が空でない場合スキップ
        imported = fresh_store.migrate_from_jsonl(skip_if_db_has_data=True)
        assert imported == 0
        # union read: db の db-only(1) + 未 ingest jsonl の jsonl-only(1) = 2
        assert fresh_store.count_unique_since("2026-01-01T00:00:00+00:00") == 2


class TestDeleteBySessionIds:
    def test_deletes_matching_session_ids(self, fresh_store):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        for sid in ("a", "b", "c"):
            fresh_store.append({"session_id": sid, "timestamp": "2026-04-30T12:00:00+00:00"})
        deleted = fresh_store.delete_by_session_ids(["a", "c"])
        assert deleted == 2
        remaining = fresh_store.query()
        assert {r["session_id"] for r in remaining} == {"b"}

    def test_source_filter_only_deletes_matching_source(self, fresh_store):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "source": "backfill"})
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "source": "live"})
        deleted = fresh_store.delete_by_session_ids(["a", "b"], source="backfill")
        assert deleted == 1
        remaining = fresh_store.query()
        assert {r["session_id"] for r in remaining} == {"b"}

    def test_empty_ids_returns_zero(self, fresh_store):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        assert fresh_store.delete_by_session_ids([]) == 0
        assert len(fresh_store.query()) == 1

    def test_jsonl_fallback_when_no_duckdb(self, fresh_store, tmp_path, monkeypatch):
        monkeypatch.setattr(fresh_store, "HAS_DUCKDB", False)
        jsonl = tmp_path / "sessions.jsonl"
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "source": "backfill"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "source": "live"},
            {"session_id": "c", "timestamp": "2026-04-30T14:00:00+00:00", "source": "backfill"},
        ]
        jsonl.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        deleted = fresh_store.delete_by_session_ids(["a", "c"], source="backfill")
        assert deleted == 2
        remaining = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert [r["session_id"] for r in remaining] == ["b"]


def _requires_duckdb():
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")


class TestIngest:
    """Phase A (#415): ingest は jsonl → db を最上位 1 connection で取り込む。

    取り込み成功後に jsonl を .ingested-<ts> へ rotate し、rotate 済みは
    glob で恒久除外（mtime 非依存）。1世代保持。
    """

    def test_ingest_imports_jsonl_into_db(self, fresh_store, tmp_path):
        _requires_duckdb()
        records = [
            {"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00", "project": "p1"},
            {"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00", "project": "p2"},
        ]
        for r in records:
            fresh_store.append(r)

        inserted = fresh_store.ingest()
        assert inserted == 2

        import duckdb
        con = duckdb.connect(str(tmp_path / "sessions.db"))
        rows = con.execute("SELECT session_id FROM sessions ORDER BY session_id").fetchall()
        con.close()
        assert [r[0] for r in rows] == ["a", "b"]

    def test_ingest_rotates_jsonl_after_success(self, fresh_store, tmp_path):
        _requires_duckdb()
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})

        fresh_store.ingest()

        # live jsonl は消える、rotate 済みファイルが残る
        assert not (tmp_path / "sessions.jsonl").exists(), "ingest 後 live jsonl は rotate される"
        rotated = list(tmp_path.glob("sessions.jsonl.ingested-*"))
        assert len(rotated) == 1

    def test_ingest_excludes_rotated_files(self, fresh_store, tmp_path):
        """rotate 済みファイルは再 ingest されない（glob 恒久除外）。"""
        _requires_duckdb()
        # 既に rotate 済みのファイルを置く
        rotated = tmp_path / "sessions.jsonl.ingested-20260101T000000"
        rotated.write_text(
            json.dumps({"session_id": "old", "timestamp": "2026-01-01T00:00:00+00:00"}) + "\n",
            encoding="utf-8",
        )
        # 新規 live jsonl
        fresh_store.append({"session_id": "new", "timestamp": "2026-04-30T12:00:00+00:00"})

        inserted = fresh_store.ingest()
        assert inserted == 1, "rotate 済みは取り込まない。live の 1 件だけ"

        import duckdb
        con = duckdb.connect(str(tmp_path / "sessions.db"))
        rows = con.execute("SELECT session_id FROM sessions").fetchall()
        con.close()
        assert [r[0] for r in rows] == ["new"]

    def test_ingest_is_idempotent_on_dedup_key(self, fresh_store, tmp_path):
        """(session_id, timestamp) 重複は二重挿入しない。"""
        _requires_duckdb()
        # 1回目
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        # 同じキーを再追記して再 ingest
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        inserted2 = fresh_store.ingest()
        assert inserted2 == 0, "既存 (session_id, timestamp) は挿入しない"

        import duckdb
        con = duckdb.connect(str(tmp_path / "sessions.db"))
        cnt = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        con.close()
        assert cnt == 1

    def test_ingest_keeps_one_generation_of_rotated(self, fresh_store, tmp_path):
        """rotate 済みは1世代保持。古い rotate は削除される。"""
        _requires_duckdb()
        # 1回目 ingest
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        # 2回目 ingest
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"})
        fresh_store.ingest()

        rotated = list(tmp_path.glob("sessions.jsonl.ingested-*"))
        assert len(rotated) == 1, "rotate 済みは1世代のみ保持"

    def test_ingest_no_jsonl_is_noop(self, fresh_store, tmp_path):
        _requires_duckdb()
        assert fresh_store.ingest() == 0

    def test_ingest_uses_single_connection(self, fresh_store, tmp_path, monkeypatch):
        """DuckDB checkpoint pitfall: ingest は最上位 1 connection。

        per-row connect を禁止する回帰テスト。duckdb.connect の呼び出し回数を数える。
        """
        _requires_duckdb()
        import duckdb as _dd

        for i in range(5):
            fresh_store.append({"session_id": f"s{i}", "timestamp": f"2026-04-30T{i:02d}:00:00+00:00"})

        calls = {"n": 0}
        real_connect = _dd.connect

        def _counting_connect(*a, **k):
            calls["n"] += 1
            return real_connect(*a, **k)

        monkeypatch.setattr(_dd, "connect", _counting_connect)
        fresh_store.ingest()
        assert calls["n"] == 1, f"ingest は 1 connection のみ。実際 {calls['n']} 回"


class TestUnionRead:
    """Phase A (#415): count_unique_since / query は union read。

    db の結果 + 未 ingest jsonl の結果を (session_id, timestamp) で dedup 合算。
    理由: trigger_engine は ingest と非同期に count を読む。
    """

    def test_count_unique_since_includes_uningested_jsonl(self, fresh_store, tmp_path):
        _requires_duckdb()
        # db 側: a を ingest 済みに
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        # jsonl 側: b は未 ingest（live jsonl にだけ存在）
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"})

        count = fresh_store.count_unique_since("2026-01-01T00:00:00+00:00")
        assert count == 2, "db の a + 未 ingest jsonl の b の合算"

    def test_count_unique_dedups_across_db_and_jsonl(self, fresh_store, tmp_path):
        """同一 (session_id, timestamp) が db と jsonl 両方にあっても 1 カウント。"""
        _requires_duckdb()
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        # 同じキーを live jsonl に再出現させる（ingest 前の中間状態を模す）
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})

        count = fresh_store.count_unique_since("2026-01-01T00:00:00+00:00")
        assert count == 1, "(session_id, timestamp) で dedup"

    def test_query_includes_uningested_jsonl(self, fresh_store, tmp_path):
        _requires_duckdb()
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"})

        results = fresh_store.query(since="2026-01-01T00:00:00+00:00")
        sids = {r["session_id"] for r in results}
        assert sids == {"a", "b"}, "db の a + 未 ingest jsonl の b"

    def test_query_dedups_and_orders(self, fresh_store, tmp_path):
        _requires_duckdb()
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        # 同一キー再出現 + 新規 b
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"})

        results = fresh_store.query(since="2026-01-01T00:00:00+00:00")
        keys = [(r["session_id"], r["timestamp"]) for r in results]
        assert keys == [
            ("a", "2026-04-30T12:00:00+00:00"),
            ("b", "2026-04-30T13:00:00+00:00"),
        ], "dedup 済みかつ timestamp 昇順"

    def test_query_limit_applies_after_union(self, fresh_store, tmp_path):
        _requires_duckdb()
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"})
        fresh_store.append({"session_id": "c", "timestamp": "2026-04-30T14:00:00+00:00"})

        results = fresh_store.query(limit=2)
        assert len(results) == 2


class TestCompaction:
    """Phase A (#415): ingest 完走時にサイズ乖離 >10倍 で rebuild。"""

    def test_compaction_rebuilds_bloated_db(self, fresh_store, tmp_path, monkeypatch):
        """file_size が rows×平均行長 の 10 倍超で CREATE TABLE AS swap rebuild。"""
        _requires_duckdb()
        # 少量データを ingest（db に sessions が入る）
        fresh_store.append({"session_id": "a", "timestamp": "2026-04-30T12:00:00+00:00"})
        fresh_store.ingest()

        db_path = tmp_path / "sessions.db"
        size_before = db_path.stat().st_size

        # db を人為的に肥大させる（bloat を模す: 大量 INSERT→DELETE で free page を残す）。
        # DuckDB はブロック単位割り当てで最小ファイルサイズの床（数百KB）があり、かつ列圧縮が
        # 効くため、床（compaction 閾値 4MB）を超える incompressible データで bloat させる。
        #
        # #457: 旧実装は 60000 行を Python ループで 1 行ずつ INSERT しており（per-row
        # con.execute + secrets.token_hex）1 件 43s かかっていた。bloat の意図（>4MB の
        # incompressible データで free page を残す）は保ったまま、DuckDB 側生成の
        # md5(random()) を bulk INSERT に置換して 0.3s 級に短縮する。
        # 肝: INSERT 後にいったん close してブロック割り当てをディスクへ flush してから
        # 別 connection で DELETE する（同一 connection 内の INSERT→DELETE は DuckDB が
        # 割り当てを最適化で畳んでしまい file が膨らまない）。free page は rebuild まで残る。
        import duckdb

        # raw_json は md5(random()) を 16 連結（≈512 hex 文字の incompressible データ）。
        _md5_concat = " || ".join(["md5(random()::VARCHAR)"] * 16)
        con = duckdb.connect(str(db_path))
        con.execute(
            f"""
            INSERT INTO sessions (session_id, timestamp, raw_json)
            SELECT 'bloat' || i::VARCHAR, '2026-04-30T00:00:00+00:00', {_md5_concat}
            FROM range(60000) t(i)
            """
        )
        con.close()  # ブロック割り当てをディスクへ flush（この後の DELETE で free page 化）
        con = duckdb.connect(str(db_path))
        con.execute("DELETE FROM sessions WHERE session_id LIKE 'bloat%'")
        con.close()
        size_bloated = db_path.stat().st_size
        assert size_bloated > size_before, "前提: bloat で db が膨らんでいる"
        assert size_bloated > 4 * 1024 * 1024, "前提: bloat が compaction 閾値(4MB)を超える"

        # 次の ingest で compaction が発火し db が縮む
        fresh_store.append({"session_id": "b", "timestamp": "2026-04-30T13:00:00+00:00"})
        result = fresh_store.ingest()
        assert result >= 0

        size_after = db_path.stat().st_size
        assert size_after < size_bloated, "compaction で db が縮む"
        # データは保全される
        con = duckdb.connect(str(db_path))
        sids = {r[0] for r in con.execute("SELECT session_id FROM sessions").fetchall()}
        con.close()
        assert sids == {"a", "b"}, "compaction 後もデータ保全"
