"""episodic_store — 直近セッションの「適用済み修正」を DuckDB で TTL 管理するストア。

3層メモリ設計 (issue #189) の episodic 層:
  working   ~/.claude/evolve-anything/corrections.jsonl  (変更なし)
  episodic  ~/.claude/evolve-anything/episodic.db         (このモジュール)
  semantic  ~/.claude/projects/<pj>/memory/*.md       (変更なし)

DuckDB 有: episodic.db の episodic_events テーブルを SoR とする。
DuckDB 無: episodic 機能を silent skip。reflect は引き続き動作する。

設計判断:
  - 昇格トリガー: reflect 適用時のみ (SNR 最大)
  - TTL デフォルト: 30 日
  - project scope: project_path フィルタ + None → 全件対象
  - retrieve: Jaccard スコアリング (tokenize() 流用、依存なし)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR: Path = (
    Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
)

EPISODIC_DB_NAME = "episodic.db"

try:
    import duckdb as _duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodic_events (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    project_path    TEXT,
    timestamp       TIMESTAMP NOT NULL,
    content         TEXT NOT NULL,
    correction_type TEXT,
    confidence      REAL,
    ttl_days        INTEGER DEFAULT 30,
    expires_at      TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodic_project ON episodic_events(project_path);
CREATE INDEX IF NOT EXISTS idx_episodic_expires ON episodic_events(expires_at);
"""


def get_db_path() -> Path:
    """現在の DATA_DIR に基づく episodic.db パスを返す。"""
    return DATA_DIR / EPISODIC_DB_NAME


def _connect():
    """DuckDB 接続を返す。スキーマを保証する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = get_db_path()
    con = _duckdb.connect(str(db_path))
    con.execute(_SCHEMA_SQL)
    # correction 内容を含む DB / WAL はオーナーのみ読み書き可能にする
    for _p in [db_path, Path(str(db_path) + ".wal")]:
        if _p.exists():
            try:
                _p.chmod(0o600)
            except OSError:
                pass
    return con


def insert_event(
    session_id: str,
    project_path: str | None,
    content: str,
    correction_type: str | None = None,
    confidence: float | None = None,
    ttl_days: int = 30,
) -> bool:
    """修正を episodic_events に挿入する。INSERT OR IGNORE で冪等。

    DuckDB 未インストール時は silent skip (False)。
    DB アクセスエラー時は stderr に warn を出して False を返す。

    Returns:
        True if the row was written, False on skip or error.
    """
    if not HAS_DUCKDB:
        return False

    now = _utcnow()
    event_id = f"{session_id}#{now.isoformat()}"
    expires_at = now + timedelta(days=max(ttl_days, 1))

    con = None
    success = False
    try:
        con = _connect()
        con.execute(
            """
            INSERT OR IGNORE INTO episodic_events
              (id, session_id, project_path, timestamp, content,
               correction_type, confidence, ttl_days, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event_id,
                session_id,
                project_path,
                now,
                content,
                correction_type,
                confidence,
                ttl_days,
                expires_at,
            ],
        )
        success = True
    except (OSError, PermissionError) as e:
        print(f"[episodic_store] DB アクセスエラー (skip): {e}", file=sys.stderr)
    except Exception as e:
        print(f"[episodic_store] insert_event 失敗 (skip): {e}", file=sys.stderr)
    finally:
        _close(con)
    return success


def query_relevant(
    keywords: set[str],
    project_path: str | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """キーワードに関連する有効期限内の episodic events を返す。

    - project_path が None の場合は全件を対象にする
    - project_path が指定された場合は完全一致 + project_path=NULL も含む
    - Jaccard スコア降順でソート

    Returns:
        [{id, content, correction_type, timestamp, days_ago, score}, ...]
        DuckDB 未インストール時・エラー時は空リスト。
    """
    if not HAS_DUCKDB or not keywords:
        return []

    # #491: read 経路で空 DB を物理生成しない（dry-run の「1バイトも書かない」契約）。
    # DB がまだ無ければ取得結果も空なので、connect/mkdir/CREATE TABLE を一切走らせない。
    if not get_db_path().exists():
        return []

    con = None
    try:
        con = _connect()
        now = _utcnow()

        if project_path is None:
            rows = con.execute(
                """
                SELECT id, content, correction_type, timestamp
                FROM episodic_events
                WHERE expires_at > ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                [now, limit * 5],
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT id, content, correction_type, timestamp
                FROM episodic_events
                WHERE expires_at > ?
                  AND (project_path = ? OR project_path IS NULL)
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                [now, project_path, limit * 5],
            ).fetchall()

        from similarity import jaccard_coefficient, tokenize

        results: list[dict[str, Any]] = []
        for row in rows:
            event_id, content, corr_type, ts = row
            event_tokens = tokenize(content)
            content_lower = content.lower()
            # Jaccard スコア（英数字トークン向け）
            score = jaccard_coefficient(keywords, event_tokens) if event_tokens else 0.0
            # 日本語など空白分割が粗い場合: recall ベースの補完スコア
            # 英語トークン: \b word-boundary で "git"→"digit" 誤マッチを防ぐ
            # 日本語トークン: \b が機能しないため substring match（_MIN_KEYWORDS>=2 で守る）
            # recall ベース: matched / total_keywords で [0, 1] に収まる
            if score == 0.0 and keywords:
                import re as _re

                def _kw_matches(kw: str, text: str) -> bool:
                    if _re.fullmatch(r"[a-z0-9]+", kw):
                        return bool(_re.search(r"\b" + _re.escape(kw) + r"\b", text))
                    return kw in text

                matched = sum(1 for kw in keywords if _kw_matches(kw.lower(), content_lower))
                if matched:
                    score = matched / len(keywords)  # recall score, always in [0, 1]
            if score > 0:
                age_days = max(0, (now - _to_utc(ts)).days)
                results.append(
                    {
                        "id": event_id,
                        "content": content,
                        "correction_type": corr_type,
                        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                        "days_ago": age_days,
                        "score": round(score, 4),
                    }
                )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    except Exception as e:
        print(f"[episodic_store] query_relevant 失敗 (skip): {e}", file=sys.stderr)
        return []
    finally:
        _close(con)


def prune_expired() -> int:
    """期限切れの episodic events を削除する。

    Returns:
        削除件数。DuckDB 未インストール時・エラー時は 0。
    """
    if not HAS_DUCKDB:
        return 0

    con = None
    try:
        con = _connect()
        now = _utcnow()
        before = con.execute("SELECT COUNT(*) FROM episodic_events").fetchone()[0]
        con.execute("DELETE FROM episodic_events WHERE expires_at <= ?", [now])
        after = con.execute("SELECT COUNT(*) FROM episodic_events").fetchone()[0]
        return int(before - after)
    except Exception as e:
        print(f"[episodic_store] prune_expired 失敗 (skip): {e}", file=sys.stderr)
        return 0
    finally:
        _close(con)


def count_events(project_path: str | None = None) -> int:
    """有効期限内の episodic events 件数を返す。テスト用ヘルパー。"""
    if not HAS_DUCKDB:
        return 0
    con = None
    try:
        con = _connect()
        now = _utcnow()
        if project_path is None:
            row = con.execute(
                "SELECT COUNT(*) FROM episodic_events WHERE expires_at > ?", [now]
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT COUNT(*) FROM episodic_events
                WHERE expires_at > ?
                  AND (project_path = ? OR project_path IS NULL)
                """,
                [now, project_path],
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        _close(con)


# ── 内部ヘルパー ────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(ts: Any) -> datetime:
    """DuckDB から取得した timestamp を UTC datetime に変換する。"""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return _utcnow()


def _close(con: Any) -> None:
    if con is not None:
        try:
            con.close()
        except Exception:
            pass
