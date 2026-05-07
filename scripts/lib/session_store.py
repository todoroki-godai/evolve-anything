"""SessionStore — sessions の永続化を集約する Repository。

DuckDB 有: sessions.db (sessions テーブル) を SoR とする
DuckDB 無: sessions.jsonl にフォールバック（後方互換）

呼び出し側は ストレージ詳細を意識せず append/count_unique_since/query を使う。
LLM 呼び出しは行わない（MUST NOT）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"
SESSIONS_DB = DATA_DIR / "sessions.db"
SESSIONS_JSONL = DATA_DIR / "sessions.jsonl"

try:
    import duckdb as _duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    project     TEXT,
    type        TEXT,
    skill_count INTEGER,
    error_count INTEGER,
    raw_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON sessions(timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
"""


def _connect():
    """DuckDB 接続を返す。スキーマを保証する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = _duckdb.connect(str(SESSIONS_DB))
    con.execute(_SCHEMA_SQL)
    return con


def append(record: dict) -> None:
    """セッションレコードを追記する。

    Phase 2: DuckDB を SoR として書く。HAS_DUCKDB=False のみ JSONL にフォールバック。
    DuckDB ロック競合時は最大 2 回リトライし、それでも失敗なら JSONL フォールバック。
    """
    if HAS_DUCKDB:
        import time as _time
        _max_retries = 2
        for attempt in range(_max_retries + 1):
            con = None
            try:
                con = _connect()
                con.execute(
                    "INSERT INTO sessions (session_id, timestamp, project, type, skill_count, error_count, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        record.get("session_id", ""),
                        record.get("timestamp", ""),
                        record.get("project"),
                        record.get("type"),
                        record.get("skill_count"),
                        record.get("error_count"),
                        json.dumps(record, ensure_ascii=False),
                    ],
                )
                return
            except Exception:
                if attempt < _max_retries:
                    _time.sleep(0.05 * (2 ** attempt))
            finally:
                if con is not None:
                    try:
                        con.close()
                    except Exception:
                        pass
        # 全リトライ失敗 → JSONL フォールバック

    _append_jsonl(record)


def _append_jsonl(record: dict) -> None:
    """sessions.jsonl に JSON 1行追記。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not SESSIONS_JSONL.exists()
        with open(SESSIONS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if is_new:
            try:
                SESSIONS_JSONL.chmod(0o600)
            except OSError:
                pass
    except OSError:
        pass


def count_unique_since(timestamp: str) -> int:
    """timestamp より後のユニーク session_id 数を返す。"""
    if HAS_DUCKDB and SESSIONS_DB.exists():
        con = None
        try:
            con = _connect()
            result = con.execute(
                "SELECT COUNT(DISTINCT session_id) FROM sessions "
                "WHERE timestamp > ? AND session_id IS NOT NULL AND session_id != ''",
                [timestamp],
            ).fetchone()
            return int(result[0]) if result else 0
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    return _count_unique_since_jsonl(timestamp)


def _count_unique_since_jsonl(timestamp: str) -> int:
    if not SESSIONS_JSONL.exists():
        return 0
    session_ids: set[str] = set()
    for line in SESSIONS_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            ts = rec.get("timestamp", "")
            if ts > timestamp:
                sid = rec.get("session_id", "")
                if sid:
                    session_ids.add(sid)
        except json.JSONDecodeError:
            continue
    return len(session_ids)


def query(since: str | None = None, limit: int | None = None) -> list[dict]:
    """セッションレコードを返す。raw_json をデコードして返す。

    Args:
        since: ISO 8601 timestamp。指定時はこれより新しいレコードのみ。
        limit: 返す件数の上限。
    """
    if HAS_DUCKDB and SESSIONS_DB.exists():
        con = None
        try:
            con = _connect()
            sql = "SELECT raw_json FROM sessions"
            params: list[Any] = []
            if since:
                sql += " WHERE timestamp > ?"
                params.append(since)
            sql += " ORDER BY timestamp"
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = con.execute(sql, params).fetchall()
            results = []
            for (raw,) in rows:
                try:
                    results.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
            return results
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    return _query_jsonl(since=since, limit=limit)


def _query_jsonl(since: str | None = None, limit: int | None = None) -> list[dict]:
    if not SESSIONS_JSONL.exists():
        return []
    results = []
    for line in SESSIONS_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and rec.get("timestamp", "") <= since:
            continue
        results.append(rec)
        if limit and len(results) >= limit:
            break
    return results


def migrate_from_jsonl(skip_if_db_has_data: bool = False) -> int:
    """sessions.jsonl のレコードを sessions.db に取り込む。

    べき等: 同じ (session_id, timestamp) ペアは重複挿入しない。

    Args:
        skip_if_db_has_data: True なら DB に既存データがある場合スキップ。

    Returns:
        新規に挿入された件数。
    """
    if not HAS_DUCKDB:
        return 0
    if not SESSIONS_JSONL.exists():
        return 0

    con = None
    try:
        con = _connect()
        if skip_if_db_has_data:
            existing = con.execute("SELECT COUNT(*) FROM sessions").fetchone()
            if existing and existing[0] > 0:
                return 0

        # 既存の (session_id, timestamp) ペアを取得して重複防止
        existing_keys = {
            (row[0], row[1])
            for row in con.execute("SELECT session_id, timestamp FROM sessions").fetchall()
        }

        inserted = 0
        for line in SESSIONS_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = rec.get("session_id", "")
            ts = rec.get("timestamp", "")
            if not sid or not ts:
                continue
            if (sid, ts) in existing_keys:
                continue
            con.execute(
                "INSERT INTO sessions (session_id, timestamp, project, type, skill_count, error_count, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    sid,
                    ts,
                    rec.get("project"),
                    rec.get("type"),
                    rec.get("skill_count"),
                    rec.get("error_count"),
                    json.dumps(rec, ensure_ascii=False),
                ],
            )
            existing_keys.add((sid, ts))
            inserted += 1
        return inserted
    except Exception:
        return 0
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def delete_by_session_ids(session_ids: list[str], source: str | None = None) -> int:
    """指定 session_id のレコードを削除する。

    Args:
        session_ids: 削除対象の session_id リスト。
        source: 指定時はこの source を持つレコードのみ削除（backfill 等）。

    Returns:
        削除件数。
    """
    if not session_ids:
        return 0

    if HAS_DUCKDB and SESSIONS_DB.exists():
        con = None
        try:
            con = _connect()
            placeholders = ",".join(["?"] * len(session_ids))
            sql = f"DELETE FROM sessions WHERE session_id IN ({placeholders})"
            params: list[Any] = list(session_ids)
            if source is not None:
                sql += " AND json_extract_string(raw_json, '$.source') = ?"
                params.append(source)
            sql += " RETURNING session_id"
            rows = con.execute(sql, params).fetchall()
            return len(rows)
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    return _delete_by_session_ids_jsonl(session_ids, source=source)


def _delete_by_session_ids_jsonl(session_ids: list[str], source: str | None = None) -> int:
    if not SESSIONS_JSONL.exists() or not session_ids:
        return 0
    target = set(session_ids)
    kept: list[str] = []
    deleted = 0
    for line in SESSIONS_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if rec.get("session_id") in target and (source is None or rec.get("source") == source):
            deleted += 1
            continue
        kept.append(line)
    SESSIONS_JSONL.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    return deleted


