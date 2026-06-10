"""utterance_archive.store — utterances.db（DuckDB）のスキーマ・接続・書き込み（#430）。

設計（design doc「スキーマ」）:
- 物理 PK = (source_path, line_no) — 増分 ingest と自然に整合
- 論理 UNIQUE = (session_id, timestamp, text_hash) — resume の履歴 replay 複製を弾く
- ingest_state(source_path, mtime, line_offset) で増分取り込み
- staleness marker: last_ingest_at ファイル（ingest 完走時に ingest 自身が書く）

DuckDB checkpoint pitfall 準拠: 最上位 1 connection を context manager で共有。
DATA_DIR は呼び出し側（ingest）が ADR-042 resolver で解決して渡す。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .extractor import Utterance

try:
    import duckdb as _duckdb  # type: ignore

    HAS_DUCKDB = True
except ImportError:  # pragma: no cover
    HAS_DUCKDB = False

# staleness marker のファイル名（DATA_DIR 直下）。
MARKER_NAME = "utterances_last_ingest_at"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS utterances (
    source_path       TEXT NOT NULL,
    line_no           INTEGER NOT NULL,
    pj_slug           TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    text              TEXT NOT NULL,
    text_hash         TEXT NOT NULL,
    prev_action       TEXT,
    source_kind       TEXT NOT NULL,
    extractor_version INTEGER NOT NULL,
    ingested_at       TEXT NOT NULL,
    PRIMARY KEY (source_path, line_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_utt_logical
    ON utterances(session_id, timestamp, text_hash);
CREATE INDEX IF NOT EXISTS idx_utt_session ON utterances(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_utt_pj_time ON utterances(pj_slug, timestamp);

CREATE TABLE IF NOT EXISTS ingest_state (
    source_path TEXT PRIMARY KEY,
    mtime       DOUBLE NOT NULL,
    line_offset INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

# 物理 PK と論理 UNIQUE の両方を尊重するため INSERT OR IGNORE 相当を使う。
# DuckDB は ON CONFLICT DO NOTHING（PK + UNIQUE 両方に効く）。
_INSERT_SQL = """
INSERT INTO utterances (
    source_path, line_no, pj_slug, session_id, timestamp, text, text_hash,
    prev_action, source_kind, extractor_version, ingested_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
"""

_UPSERT_STATE_SQL = """
INSERT INTO ingest_state (source_path, mtime, line_offset, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (source_path) DO UPDATE SET
    mtime = EXCLUDED.mtime,
    line_offset = EXCLUDED.line_offset,
    updated_at = EXCLUDED.updated_at
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(db_path: Path) -> Iterator[Any]:
    """utterances.db への 1 connection を with-block 全体で共有する。

    file ごとに connect/close を繰り返さず checkpoint を 1 回に集約する
    （DuckDB checkpoint pitfall, #28）。DuckDB 未インストールなら None を yield。
    """
    if not HAS_DUCKDB:
        yield None
        return
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _duckdb.connect(str(db_path))
    con.execute(_SCHEMA_SQL)
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:
            pass


def _utt_params(u: Utterance, ingested_at: str) -> list:
    return [
        u.source_path, u.line_no, u.pj_slug, u.session_id, u.timestamp,
        u.text, u.text_hash, u.prev_action, u.source_kind,
        u.extractor_version, ingested_at,
    ]


def insert_utterances(con: Any, utterances: Sequence[Utterance]) -> int:
    """発話を bulk INSERT。物理 PK + 論理 UNIQUE で冪等。新規挿入数を返す。"""
    if con is None or not utterances:
        return 0
    ingested_at = _now_iso()
    before = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    # ON CONFLICT DO NOTHING は同一 batch 内の論理重複には効かない（同 tx 未 commit 行は
    # index に未反映なことがある）ため、batch 内でも text_hash/session/timestamp と
    # source_path/line_no を 1 件ずつ INSERT して conflict を確実に拾う。
    for u in utterances:
        con.execute(_INSERT_SQL, _utt_params(u, ingested_at))
    after = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    return int(after - before)


def get_ingest_state(con: Any) -> Dict[str, Tuple[float, int]]:
    """ingest_state 全件を {source_path: (mtime, line_offset)} で返す。"""
    if con is None:
        return {}
    rows = con.execute(
        "SELECT source_path, mtime, line_offset FROM ingest_state"
    ).fetchall()
    return {sp: (float(mt), int(off)) for sp, mt, off in rows}


def upsert_ingest_state(con: Any, source_path: str, mtime: float, line_offset: int) -> None:
    """1 ファイルの ingest 進捗を upsert。"""
    if con is None:
        return
    con.execute(_UPSERT_STATE_SQL, [source_path, float(mtime), int(line_offset), _now_iso()])


# --- staleness marker --------------------------------------------------------

def _marker_path(data_dir: Path) -> Path:
    return Path(data_dir) / MARKER_NAME


def write_last_ingest_at(data_dir: Path, ts: Optional[str] = None) -> None:
    """ingest 完走時に最終 ingest 時刻を marker に書く。"""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    _marker_path(data_dir).write_text(ts or _now_iso(), encoding="utf-8")


def read_last_ingest_at(data_dir: Path) -> Optional[str]:
    """marker の最終 ingest 時刻を返す。不在なら None（= 未 ingest）。"""
    p = _marker_path(data_dir)
    try:
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_stale(data_dir: Path, threshold_days: float = 14) -> bool:
    """最終 ingest が threshold_days より古い、または marker 不在なら True。

    marker 不在 = 「未 ingest」と解釈して stale（∞ 扱い）。null-safe 誤実装を封じる。
    """
    ts = read_last_ingest_at(data_dir)
    if ts is None:
        return True
    try:
        last = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400.0
    return age_days > threshold_days
