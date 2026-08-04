"""fitness_history_store — fitness スコアの時系列記録 SoR。

DuckDB 有: token_usage.db の fitness_history テーブルを使用。
DuckDB 無: 記録をスキップ（query は空リストを返す）。

設計: issue #240 Phase 1 参照。
token_usage_store.py と同じ token_usage.db を共有。
ON CONFLICT DO NOTHING で冪等（同 run_id の二重記録防止）。

provenance（#316）: axis ごとの評価実行条件（``scripts/lib/evaluation_provenance.py``、#309）を
JSON で持つ列。constitutional だけ LLM judge・他は決定論と axis ごとに条件が異なるため、
run 単位でなく axis 単位（行単位）で持つ。既存 DB は ``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`` で追加する（CTAS で作り直すと PK/UNIQUE 制約を落とす既知の罠があるため使わない・
#156/#157）。既存行の provenance は遡及埋めしない（NULL のまま・読み側は None を返す）。
"""
from __future__ import annotations

import json
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

# 既存 DB（provenance 列導入前）へ列を追加する migration（#316）。CREATE TABLE IF NOT
# EXISTS はテーブルが既に存在すると no-op のため、新列の追加は別途 ALTER で行う。
# ADD COLUMN IF NOT EXISTS は新規 DB（列が既にある）でも冪等。
_MIGRATE_SQL = """
ALTER TABLE fitness_history ADD COLUMN IF NOT EXISTS provenance TEXT;
"""

_INSERT_SQL = """
INSERT INTO fitness_history (run_id, timestamp, axis, score, weight_used, source, provenance)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
"""


def _provenance_mod():
    """scripts/lib/evaluation_provenance を遅延 import する（#309/#316）。"""
    import evaluation_provenance as _ep

    return _ep


def _connect():
    """DuckDB 接続を返す。スキーマを保証する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = _duckdb.connect(str(USAGE_DB))
    con.execute(_SCHEMA_SQL)
    con.execute(_MIGRATE_SQL)
    return con


def record_fitness_run(
    run_id: str,
    axis_scores: dict[str, float],
    weights: dict[str, float],
    source: str = "audit",
    provenance: "dict[str, dict[str, Any]] | None" = None,
) -> None:
    """audit --fitness environment 後に呼ぶ。冪等（同 run_id は INSERT OR IGNORE）。

    Args:
        run_id: UUID 文字列。同 run_id が既存なら何もしない。
        axis_scores: {'coherence': 0.72, 'telemetry': 0.55, ...}
        weights: {'coherence': 0.25, 'telemetry': 0.45, ...}
        source: 記録元識別子（デフォルト 'audit'）
        provenance: axis 名 -> evaluation_provenance envelope（#309/#316）。
            axis が無い / None なら writer 直前で ``finalize_provenance(None)`` を通し
            ``evaluation_kind=unknown`` として明示的に記録する（NULL のまま黙って落とさない）。
    """
    if not HAS_DUCKDB:
        return
    if not axis_scores:
        return
    if not all(math.isfinite(v) for v in axis_scores.values()):
        return

    ts = datetime.now(timezone.utc).isoformat()
    params: list[tuple[Any, ...]] = []

    try:
        ep = _provenance_mod()
    except Exception:
        ep = None

    # overall は axis_scores に含まれる場合も記録
    for axis, score in axis_scores.items():
        weight = weights.get(axis)
        axis_prov = provenance.get(axis) if provenance else None
        if ep is not None:
            try:
                prov_json = json.dumps(ep.finalize_provenance(axis_prov), ensure_ascii=False)
            except Exception:
                prov_json = None
        else:
            # evaluation_provenance 自体が import できない場合は捏造せず、
            # 渡された値をそのまま JSON 化する（無ければ NULL）。
            prov_json = (
                json.dumps(axis_prov, ensure_ascii=False)
                if isinstance(axis_prov, dict)
                else None
            )
        params.append((run_id, ts, axis, float(score), weight, source, prov_json))

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
        [{'run_id', 'timestamp', 'axis', 'score', 'weight_used', 'source', 'provenance'}, ...]
        provenance は dict または None（旧行=列導入前の記録は遡及埋めせず None・#316）。
    """
    if not HAS_DUCKDB:
        return []
    if not USAGE_DB.exists():
        return []

    sql = """
    SELECT run_id, timestamp, axis, score, weight_used, source, provenance
    FROM fitness_history
    WHERE axis = ?
    ORDER BY id DESC
    LIMIT ?
    """
    c = None
    try:
        c = _connect()
        rows = c.execute(sql, [axis, int(limit)]).fetchall()
        out = []
        for r in rows:
            try:
                prov = json.loads(r[6]) if r[6] else None
            except Exception:
                prov = None
            out.append(
                {
                    "run_id": r[0],
                    "timestamp": r[1],
                    "axis": r[2],
                    "score": r[3],
                    "weight_used": r[4],
                    "source": r[5],
                    "provenance": prov,
                }
            )
        return out
    except Exception:
        return []
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
