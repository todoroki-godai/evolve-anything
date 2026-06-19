"""fitness_history_store — fitness スコアの時系列記録 SoR。

DuckDB 有: token_usage.db の fitness_history テーブルを使用。
DuckDB 無: 記録をスキップ（query は空リストを返す）。

設計: issue #240 Phase 1 参照。
token_usage_store.py と同じ token_usage.db を共有。
ON CONFLICT DO NOTHING で冪等（同 run_id の二重記録防止）。
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
USAGE_DB = DATA_DIR / "token_usage.db"

try:
    import duckdb as _duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


_SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS fitness_history_id_seq;
CREATE TABLE IF NOT EXISTS fitness_history (
    id        BIGINT DEFAULT nextval('fitness_history_id_seq'),
    run_id    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    axis      TEXT NOT NULL,
    score     REAL NOT NULL,
    weight_used REAL,
    source    TEXT DEFAULT 'audit',
    UNIQUE (run_id, axis)
);
"""

_INSERT_SQL = """
INSERT INTO fitness_history (run_id, timestamp, axis, score, weight_used, source)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
"""


def _connect():
    """DuckDB 接続を返す。スキーマを保証する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = _duckdb.connect(str(USAGE_DB))
    con.execute(_SCHEMA_SQL)
    return con


def record_fitness_run(
    run_id: str,
    axis_scores: dict[str, float],
    weights: dict[str, float],
    source: str = "audit",
) -> None:
    """audit --fitness environment 後に呼ぶ。冪等（同 run_id は INSERT OR IGNORE）。

    Args:
        run_id: UUID 文字列。同 run_id が既存なら何もしない。
        axis_scores: {'coherence': 0.72, 'telemetry': 0.55, ...}
        weights: {'coherence': 0.25, 'telemetry': 0.45, ...}
        source: 記録元識別子（デフォルト 'audit'）
    """
    if not HAS_DUCKDB:
        return
    if not axis_scores:
        return
    if not all(math.isfinite(v) for v in axis_scores.values()):
        return

    ts = datetime.now(timezone.utc).isoformat()
    params: list[tuple[Any, ...]] = []

    # overall は axis_scores に含まれる場合も記録
    for axis, score in axis_scores.items():
        weight = weights.get(axis)
        params.append((run_id, ts, axis, float(score), weight, source))

    c = None
    try:
        c = _connect()
        c.executemany(_INSERT_SQL, params)
    except Exception:
        pass
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def get_axis_history(axis: str, limit: int = 20) -> list[dict]:
    """過去 N 回の axis スコアを新しい順で返す。

    Args:
        axis: 'coherence'|'telemetry'|'constitutional'|'skill_quality'|'overall'
        limit: 取得件数上限

    Returns:
        [{'run_id', 'timestamp', 'axis', 'score', 'weight_used', 'source'}, ...]
    """
    if not HAS_DUCKDB:
        return []
    if not USAGE_DB.exists():
        return []

    sql = """
    SELECT run_id, timestamp, axis, score, weight_used, source
    FROM fitness_history
    WHERE axis = ?
    ORDER BY id DESC
    LIMIT ?
    """
    c = None
    try:
        c = _connect()
        rows = c.execute(sql, [axis, int(limit)]).fetchall()
        return [
            {
                "run_id": r[0],
                "timestamp": r[1],
                "axis": r[2],
                "score": r[3],
                "weight_used": r[4],
                "source": r[5],
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
