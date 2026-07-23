"""codex CLI 利用状況の read-only 集計テスト（#245）。

`~/.codex/state_5.sqlite` の `threads` テーブルを read-only で読み、PJ 別
セッション数/tokens_used/最終利用時刻を集計する。3つの degrade ケース
（DB 不在 / open失敗・ロック / スキーマ相違）を必ず検証する。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from fleet.codex_usage import (  # noqa: E402
    CODEX_STATUS_LOCKED,
    CODEX_STATUS_MISSING,
    CODEX_STATUS_OK,
    CODEX_STATUS_SCHEMA_MISMATCH,
    collect_codex_usage,
)


def _make_db(path: Path, rows: list[tuple]) -> None:
    """最小の `threads` テーブルを持つ実 sqlite ファイルを作る。

    rows: (id, cwd, tokens_used, updated_at, updated_at_ms) のタプル列。
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            cwd TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            updated_at_ms INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO threads (id, cwd, tokens_used, updated_at, updated_at_ms) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class TestMissingDb:
    def test_nonexistent_db_is_silent_missing(self, tmp_path):
        """① DB 不在 → 無音 skip（status=missing, by_project 空）。"""
        result = collect_codex_usage(db_path=tmp_path / "state_5.sqlite")
        assert result.status == CODEX_STATUS_MISSING
        assert result.by_project == {}


class TestLockedOrOpenFailure:
    def test_connect_failure_is_fail_open_locked(self, tmp_path, monkeypatch):
        """② open 失敗（ロック等）→ fail-open で status=locked、例外は投げない。"""
        db_path = tmp_path / "state_5.sqlite"
        db_path.write_text("not a real sqlite file")  # exists だが破損

        result = collect_codex_usage(db_path=db_path)
        assert result.status == CODEX_STATUS_LOCKED
        assert result.error is not None
        assert result.by_project == {}


class TestSchemaMismatch:
    def test_missing_threads_table_is_schema_mismatch(self, tmp_path):
        """③ threads テーブル自体が無い → status=schema_mismatch、例外は投げない。"""
        db_path = tmp_path / "state_5.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE unrelated (id TEXT)")
        conn.commit()
        conn.close()

        result = collect_codex_usage(db_path=db_path)
        assert result.status == CODEX_STATUS_SCHEMA_MISMATCH
        assert result.by_project == {}

    def test_missing_column_is_schema_mismatch(self, tmp_path):
        """threads はあるが列が欠けている（旧/新バージョン差異）→ schema_mismatch。"""
        db_path = tmp_path / "state_5.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT)")
        conn.commit()
        conn.close()

        result = collect_codex_usage(db_path=db_path)
        assert result.status == CODEX_STATUS_SCHEMA_MISMATCH


class TestAggregation:
    def test_aggregates_sessions_and_tokens_by_project(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        db_path = tmp_path / "state_5.sqlite"
        pj_a = tmp_path / "pj-a"
        pj_a.mkdir()
        _make_db(
            db_path,
            [
                ("t1", str(pj_a), 1000, int(now.timestamp()), _ms(now)),
                ("t2", str(pj_a), 2000, int(now.timestamp()), _ms(now - timedelta(days=1))),
            ],
        )
        # _normalize_pj の import 失敗フォールバック（Path.name）でも通るよう slug は basename 一致を仮定
        monkeypatch.setattr(
            "audit.outcome_metrics._normalize_pj", lambda v: Path(str(v)).name if v else None
        )

        result = collect_codex_usage(db_path=db_path, now=now)
        assert result.status == CODEX_STATUS_OK
        assert "pj-a" in result.by_project
        entry = result.by_project["pj-a"]
        assert entry["sessions"] == 2
        assert entry["tokens_used"] == 3000
        assert entry["last_used"] is not None

    def test_window_days_filters_old_sessions(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        db_path = tmp_path / "state_5.sqlite"
        pj_a = tmp_path / "pj-a"
        pj_a.mkdir()
        old = now - timedelta(days=90)
        _make_db(
            db_path,
            [("t1", str(pj_a), 500, int(old.timestamp()), _ms(old))],
        )
        monkeypatch.setattr(
            "audit.outcome_metrics._normalize_pj", lambda v: Path(str(v)).name if v else None
        )

        result = collect_codex_usage(db_path=db_path, window_days=30, now=now)
        assert result.status == CODEX_STATUS_OK
        assert result.by_project == {}

    def test_empty_sessions_table_is_ok_but_empty(self, tmp_path):
        """sessions 空（threads 0行）→ status=ok・by_project 空（既存表示を壊さない）。"""
        db_path = tmp_path / "state_5.sqlite"
        _make_db(db_path, [])

        result = collect_codex_usage(db_path=db_path)
        assert result.status == CODEX_STATUS_OK
        assert result.by_project == {}

    def test_null_updated_at_ms_falls_back_to_updated_at(self, tmp_path, monkeypatch):
        """updated_at_ms が NULL（旧データ）でも updated_at(秒) から補完して拾う。"""
        now = datetime.now(timezone.utc)
        db_path = tmp_path / "state_5.sqlite"
        pj_a = tmp_path / "pj-a"
        pj_a.mkdir()
        _make_db(db_path, [("t1", str(pj_a), 100, int(now.timestamp()), None)])
        monkeypatch.setattr(
            "audit.outcome_metrics._normalize_pj", lambda v: Path(str(v)).name if v else None
        )

        result = collect_codex_usage(db_path=db_path, now=now)
        assert result.status == CODEX_STATUS_OK
        assert result.by_project["pj-a"]["tokens_used"] == 100


class TestMalformedRowFailOpen:
    """SQLite の動的型付け起因の型不正行が集計全体を落とさないことを検証（#245 レビュー指摘）。"""

    def test_string_tokens_used_row_is_skipped_but_others_survive(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        db_path = tmp_path / "state_5.sqlite"
        pj_a = tmp_path / "pj-a"
        pj_a.mkdir()
        _make_db(
            db_path,
            [
                ("bad", str(pj_a), "not-a-number", int(now.timestamp()), _ms(now)),
                ("good", str(pj_a), 500, int(now.timestamp()), _ms(now)),
            ],
        )
        monkeypatch.setattr(
            "audit.outcome_metrics._normalize_pj", lambda v: Path(str(v)).name if v else None
        )

        result = collect_codex_usage(db_path=db_path, now=now)
        assert result.status == CODEX_STATUS_OK
        assert result.by_project["pj-a"]["sessions"] == 1  # bad 行は skip
        assert result.by_project["pj-a"]["tokens_used"] == 500

    def test_huge_timestamp_row_is_skipped_but_others_survive(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        db_path = tmp_path / "state_5.sqlite"
        pj_a = tmp_path / "pj-a"
        pj_a.mkdir()
        huge_ts = 999999999999999  # ms 換算で datetime.fromtimestamp の表現域を超える（OverflowError）
        _make_db(
            db_path,
            [
                ("bad", str(pj_a), 100, int(now.timestamp()), huge_ts),
                ("good", str(pj_a), 200, int(now.timestamp()), _ms(now)),
            ],
        )
        monkeypatch.setattr(
            "audit.outcome_metrics._normalize_pj", lambda v: Path(str(v)).name if v else None
        )

        result = collect_codex_usage(db_path=db_path, now=now, window_days=36500)
        assert result.status == CODEX_STATUS_OK
        # 巨大 ts 行は skip される（例外にならず集計全体が継続する）
        assert result.by_project["pj-a"]["sessions"] == 1
        assert result.by_project["pj-a"]["tokens_used"] == 200

    def test_normalize_pj_raising_row_is_skipped_but_others_survive(self, tmp_path, monkeypatch):
        now = datetime.now(timezone.utc)
        db_path = tmp_path / "state_5.sqlite"
        pj_a = tmp_path / "pj-a"
        pj_a.mkdir()
        _make_db(
            db_path,
            [
                ("bad", "raise-me", 100, int(now.timestamp()), _ms(now)),
                ("good", str(pj_a), 200, int(now.timestamp()), _ms(now)),
            ],
        )

        def _flaky_normalize(v):
            if v == "raise-me":
                raise RuntimeError("boom")
            return Path(str(v)).name if v else None

        monkeypatch.setattr("audit.outcome_metrics._normalize_pj", _flaky_normalize)

        result = collect_codex_usage(db_path=db_path, now=now)
        assert result.status == CODEX_STATUS_OK
        assert "pj-a" in result.by_project
        assert result.by_project["pj-a"]["sessions"] == 1

    def test_unexpected_exception_during_collect_falls_back_to_locked(self, tmp_path, monkeypatch):
        """collect 全体の broad except 保険: sqlite3.Error 以外の予期しない例外も fail-open。"""
        db_path = tmp_path / "state_5.sqlite"
        _make_db(db_path, [])

        import fleet.codex_usage as codex_usage_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("unexpected boom")

        monkeypatch.setattr(codex_usage_mod.sqlite3, "connect", _boom)

        result = collect_codex_usage(db_path=db_path)
        assert result.status == CODEX_STATUS_LOCKED
        assert "unexpected boom" in result.error

    def test_uri_special_char_path_is_quoted(self, tmp_path):
        """db_path に URI 予約文字（?/#）が含まれても quote() で正しく open できる。"""
        weird_dir = tmp_path / "weird?dir#here"
        weird_dir.mkdir()
        db_path = weird_dir / "state_5.sqlite"
        _make_db(db_path, [])

        result = collect_codex_usage(db_path=db_path)
        assert result.status == CODEX_STATUS_OK


class TestFormatter:
    def test_format_missing_or_schema_mismatch_is_silent(self):
        from fleet.codex_usage import CodexUsageResult
        from fleet.formatters import format_codex_usage_section

        assert format_codex_usage_section(CodexUsageResult(status=CODEX_STATUS_MISSING)) == ""
        assert (
            format_codex_usage_section(CodexUsageResult(status=CODEX_STATUS_SCHEMA_MISMATCH))
            == ""
        )

    def test_format_ok_but_empty_is_silent(self):
        from fleet.codex_usage import CodexUsageResult
        from fleet.formatters import format_codex_usage_section

        assert format_codex_usage_section(CodexUsageResult(status=CODEX_STATUS_OK, by_project={})) == ""

    def test_format_locked_prints_one_warning_line(self):
        from fleet.codex_usage import CodexUsageResult
        from fleet.formatters import format_codex_usage_section

        out = format_codex_usage_section(
            CodexUsageResult(status=CODEX_STATUS_LOCKED, error="database is locked")
        )
        assert out.count("\n") == 1  # 1行のみ
        assert "database is locked" in out

    def test_format_ok_with_data_shows_summary_and_no_merge_notice(self):
        from fleet.codex_usage import CodexUsageResult
        from fleet.formatters import format_codex_usage_section

        now = datetime.now(timezone.utc)
        result = CodexUsageResult(
            status=CODEX_STATUS_OK,
            by_project={
                "pj-a": {
                    "sessions": 3,
                    "tokens_used": 1_200_000,
                    "last_used": now.isoformat(),
                }
            },
        )
        out = format_codex_usage_section(result, now=now)
        assert "pj-a" in out
        assert "3" in out
        assert "1.2M" in out
        assert "合算していません" in out

    def test_format_malformed_last_used_returns_empty_not_raise(self):
        """last_used が ISO 不正文字列でも例外を投げず空文字列に fail-open する（#245 レビュー指摘）。"""
        from fleet.codex_usage import CodexUsageResult
        from fleet.formatters import format_codex_usage_section

        result = CodexUsageResult(
            status=CODEX_STATUS_OK,
            by_project={"pj-a": {"sessions": 1, "tokens_used": 100, "last_used": "not-an-iso-date"}},
        )
        assert format_codex_usage_section(result) == ""

    def test_format_malformed_entry_shape_returns_empty_not_raise(self):
        """by_project の値が dict でない（破損データ）場合も例外を投げず空文字列にする。"""
        from fleet.codex_usage import CodexUsageResult
        from fleet.formatters import format_codex_usage_section

        result = CodexUsageResult(status=CODEX_STATUS_OK, by_project={"pj-a": "not-a-dict"})
        assert format_codex_usage_section(result) == ""
