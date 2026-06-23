"""TokenUsageStore — PJ別 LLM トークン使用量の SoR。

DuckDB 有: token_usage.db (token_usage + session_progress テーブル) を SoR とする
DuckDB 無: token_usage.jsonl にフォールバック (append のみ、query は NotImplementedError)

設計: docs/decisions + design-redesign-20260509-101410.md (issue #28) 参照。
PK は transcript 各行 top-level uuid。INSERT OR IGNORE で冪等。

issue #28 対応: ingest_all_projects から `connection()` context manager で
1 connection 共有を可能に。9925 file × close() = 9925 checkpoint の write amplification を回避。
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
USAGE_DB = DATA_DIR / "token_usage.db"
USAGE_JSONL = DATA_DIR / "token_usage.jsonl"

try:
    import duckdb as _duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    uuid                          VARCHAR PRIMARY KEY,
    ts                            TIMESTAMP NOT NULL,
    pj_id                         VARCHAR NOT NULL,
    pj_slug                       VARCHAR,
    session_id                    VARCHAR NOT NULL,
    parent_uuid                   VARCHAR,
    is_sidechain                  BOOLEAN DEFAULT FALSE,
    model                         VARCHAR,
    role                          VARCHAR,
    input_tokens                  INTEGER DEFAULT 0,
    output_tokens                 INTEGER DEFAULT 0,
    cache_creation_input_tokens   INTEGER DEFAULT 0,
    cache_read_input_tokens       INTEGER DEFAULT 0,
    web_search_requests           INTEGER DEFAULT 0,
    web_fetch_requests            INTEGER DEFAULT 0,
    ingested_at                   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_usage_pj_ts ON token_usage(pj_id, ts);
CREATE TABLE IF NOT EXISTS session_progress (
    pj_id      VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    last_uuid  VARCHAR,
    last_ts    TIMESTAMP,
    PRIMARY KEY (pj_id, session_id)
);
"""


_INSERT_SQL = """
INSERT OR IGNORE INTO token_usage (
    uuid, ts, pj_id, pj_slug, session_id, parent_uuid, is_sidechain,
    model, role,
    input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens,
    web_search_requests, web_fetch_requests
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_FIELDS = [
    "uuid", "ts", "pj_id", "pj_slug", "session_id", "parent_uuid", "is_sidechain",
    "model", "role",
    "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
    "web_search_requests", "web_fetch_requests",
]


def _connect():
    """DuckDB 接続を返す。スキーマを保証する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = _duckdb.connect(str(USAGE_DB))
    con.execute(_SCHEMA_SQL)
    return con


def _connect_ro():
    """**読み取り専用**接続（read_only=True）を返す（#65）。

    schema 作成（CREATE TABLE）・mkdir を行わず、ファイルを書き換えない。実 DB の初回
    read-write open は write transaction commit でファイル byte を書き換えるため
    （dry-run byte 契約 #461 違反・dogfood Layer 1a 赤）、read 経路はこの read_only 接続を使う。
    呼び出し側が ``USAGE_DB.exists()`` で事前ガードする前提（``token_usage_query._safe_query``）。
    """
    return _duckdb.connect(str(USAGE_DB), read_only=True)


@contextmanager
def connection() -> Iterator[Any]:
    """1 つの connection を with-block 全体で再利用するための context manager。

    用途: `ingest_all_projects` のような大量 file 処理。
    file ごとに `_connect()/close()` を繰り返すと DuckDB が close 時に checkpoint
    (= 全データ flush) を実行し write amplification を起こす (issue #28: 9925 files で
    O(N) checkpoint)。1 connection を共有することで checkpoint を 1 回に集約する。

    DuckDB 未インストール時は None を yield (JSONL fallback で connection 不要)。
    """
    if not HAS_DUCKDB:
        yield None
        return
    con = _connect()
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:
            pass


def _normalize_record_params(rec: dict) -> list:
    """token_usage record → INSERT params リストに正規化。

    DRY: append_batch / 新規テストで共有。
    None → 0/False の補正を 1 箇所に集約 (17 フィールド mapping のうち 7 箇所)。
    """
    params = [rec.get(f) for f in _INSERT_FIELDS]
    if params[6] is None:  # is_sidechain
        params[6] = False
    for idx in (9, 10, 11, 12, 13, 14):  # 5 つの token カウント + web_*_requests
        if params[idx] is None:
            params[idx] = 0
    return params


def _append_batch_with_con(con, records: list[dict]) -> int:
    """既存 connection を使って bulk INSERT。冪等性のため before/after diff で挿入数を返す。"""
    if not records:
        return 0
    before = con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
    con.executemany(_INSERT_SQL, [_normalize_record_params(r) for r in records])
    after = con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
    return int(after - before)


def append_batch(records: list[dict], con=None) -> int:
    """token_usage レコードのバッチ追記。INSERT OR IGNORE で冪等。

    Args:
        records: token_usage record の list
        con: None なら内部で短命 connection を作成 (現挙動互換)。
             外部 connection を渡すと再利用 (ingest_all_projects 経由のとき)。

    Returns:
        新規挿入された行数 (重複は含まない)。
    """
    if not records:
        return 0

    if not HAS_DUCKDB:
        return _append_batch_jsonl(records)

    # 外部 con 指定時はそのまま使う (ingest path)
    if con is not None:
        return _append_batch_with_con(con, records)

    # 短命 connection path (テスト互換 + 単発呼び出し)
    import time as _time
    _max_retries = 2
    for attempt in range(_max_retries + 1):
        c = None
        try:
            c = _connect()
            return _append_batch_with_con(c, records)
        except Exception:
            if attempt < _max_retries:
                _time.sleep(0.05 * (2 ** attempt))
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
    # 全リトライ失敗 → JSONL フォールバック
    return _append_batch_jsonl(records)


def _append_batch_jsonl(records: list[dict]) -> int:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not USAGE_JSONL.exists()
        with open(USAGE_JSONL, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if is_new:
            try:
                USAGE_JSONL.chmod(0o600)
            except OSError:
                pass
        return len(records)
    except OSError:
        return 0


def get_last_ingested_ts(pj_id: str, con=None) -> str | None:
    """指定 PJ の最大 ts を ISO 8601 で返す。データなしなら None。"""
    if not HAS_DUCKDB:
        return None

    def _do(c):
        row = c.execute(
            "SELECT MAX(ts) FROM token_usage WHERE pj_id = ?", [pj_id]
        ).fetchone()
        if row and row[0]:
            ts = row[0]
            if hasattr(ts, "isoformat"):
                return ts.isoformat()
            return str(ts)
        return None

    if con is not None:
        try:
            return _do(con)
        except Exception:
            return None

    if not USAGE_DB.exists():
        return None
    c = None
    try:
        c = _connect()
        return _do(c)
    except Exception:
        return None
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def query(sql: str, params: list[Any] | None = None, con=None) -> list[tuple]:
    """生 SQL を実行して結果を返す。DuckDB 必須。"""
    if not HAS_DUCKDB:
        raise NotImplementedError("query requires DuckDB")
    if con is not None:
        return con.execute(sql, params or []).fetchall()
    c = None
    try:
        c = _connect_ro()  # #65: read は read_only 接続（byte を書き換えない）
        return c.execute(sql, params or []).fetchall()
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


# ── session_progress: jsonl 単位の差分 ingest 用カーソル ────────────────────

def get_session_progress_for_pj(con, pj_id: str) -> dict[str, tuple]:
    """PJ 全 session の (last_uuid, last_ts) を一括取得。

    Returns: {session_id: (last_uuid, last_ts_iso_or_None)}
    """
    if not HAS_DUCKDB or con is None:
        return {}
    try:
        rows = con.execute(
            "SELECT session_id, last_uuid, last_ts FROM session_progress WHERE pj_id = ?",
            [pj_id],
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, tuple] = {}
    for sid, last_uuid, last_ts in rows:
        ts_str = last_ts.isoformat() if hasattr(last_ts, "isoformat") else (str(last_ts) if last_ts else None)
        out[sid] = (last_uuid, ts_str)
    return out


_UPSERT_PROGRESS_SQL = """
INSERT INTO session_progress (pj_id, session_id, last_uuid, last_ts)
VALUES (?, ?, ?, ?)
ON CONFLICT (pj_id, session_id) DO UPDATE SET
    last_uuid = EXCLUDED.last_uuid,
    last_ts   = EXCLUDED.last_ts
"""


def upsert_session_progress_batch(con, pj_id: str, entries: list[tuple[str, str, str | None]]) -> int:
    """session_progress を bulk upsert。

    Args:
        entries: [(session_id, last_uuid, last_ts), ...]
    """
    if not HAS_DUCKDB or con is None or not entries:
        return 0
    params = [(pj_id, sid, uuid, ts) for sid, uuid, ts in entries]
    con.executemany(_UPSERT_PROGRESS_SQL, params)
    return len(params)
