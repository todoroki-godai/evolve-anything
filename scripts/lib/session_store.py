"""SessionStore — sessions の永続化を集約する Repository。

書き込みパターン（#415 Phase A: jsonl-first 化）:
- hot path（hooks）: `append()` は **sessions.jsonl に追記するのみ**。DuckDB には書かない。
  per-fire connect→INSERT→close が sessions.db の再肥大（9.6GB / 実データ14MB）の病巣だった
  ため根治した。
- cold path（batch）: `ingest()` だけが jsonl → sessions.db を **最上位 1 connection** で
  取り込む（DuckDB checkpoint pitfall 準拠）。取り込み成功後に jsonl を `.ingested-<ts>` へ
  rotate し、rotate 済みは glob で恒久除外（mtime 非依存）。1世代保持。

読み取りパターン（union read）:
- `count_unique_since` / `query` は **db の結果 + 未 ingest jsonl の結果** を
  (session_id, timestamp) で dedup して合算する。理由: trigger_engine 等は ingest と
  **非同期**（セッションイベント時）に count を読むため、「ingest 直後にしか読まない」
  仮定は成立しない。db 不在 / DuckDB 無の場合は jsonl のみを読む（後方互換）。

LLM 呼び出しは行わない（MUST NOT）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
SESSIONS_DB = DATA_DIR / "sessions.db"
SESSIONS_JSONL = DATA_DIR / "sessions.jsonl"
# rotate 済み jsonl の glob パターン（ingest 対象から恒久除外する）。
_ROTATED_GLOB = "sessions.jsonl.ingested-*"
# 保険 compaction: file_size が rows×平均行長 のこの倍率を超えたら rebuild。
_COMPACTION_BLOAT_RATIO = 10.0
# DuckDB はブロック単位割り当てで最小ファイルサイズの床（数百KB）がある。
# この床近辺では乖離比が常に大きく出て false compaction を招くため、
# 絶対サイズがこの閾値未満なら compaction しない（縮める余地が無い）。
_COMPACTION_MIN_BYTES = 4 * 1024 * 1024  # 4MB

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

    #415 Phase A: **jsonl 追記のみ**。DuckDB へは書かない（hot path から接続を消す）。
    db への取り込みは batch `ingest()` が行う。
    """
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


def _record_to_row(rec: dict) -> list:
    """jsonl レコードを sessions テーブルの行タプルに変換。"""
    return [
        rec.get("session_id", ""),
        rec.get("timestamp", ""),
        rec.get("project"),
        rec.get("type"),
        rec.get("skill_count"),
        rec.get("error_count"),
        json.dumps(rec, ensure_ascii=False),
    ]


def _iter_jsonl_records(path: Path):
    """jsonl の各行を dict として yield（壊れた行はスキップ）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def ingest() -> int:
    """sessions.jsonl を sessions.db に batch 取り込みする。

    - 最上位 **1 connection** で取り込む（DuckDB checkpoint pitfall: per-row connect 禁止）。
    - 重複除去キーは `(session_id, timestamp)`（既存 `migrate_from_jsonl` と同一）。
    - 取り込み成功確認後に live jsonl を `.ingested-<ts>` へ rotate（rotate 済みは glob で
      恒久除外。1世代保持）。
    - 完走時に保険 compaction（サイズ乖離 >10倍 で rebuild）。

    Returns:
        新規に挿入された件数（live jsonl が無ければ 0）。
    """
    if not HAS_DUCKDB:
        return 0
    if not SESSIONS_JSONL.exists():
        # live が無くても compaction の機会としては使えるが、ここでは noop に倒す。
        return 0

    con = None
    try:
        con = _connect()
        existing_keys = {
            (row[0], row[1])
            for row in con.execute("SELECT session_id, timestamp FROM sessions").fetchall()
        }

        inserted = 0
        for rec in _iter_jsonl_records(SESSIONS_JSONL):
            sid = rec.get("session_id", "")
            ts = rec.get("timestamp", "")
            if not sid or not ts:
                continue
            if (sid, ts) in existing_keys:
                continue
            con.execute(
                "INSERT INTO sessions (session_id, timestamp, project, type, skill_count, error_count, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                _record_to_row(rec),
            )
            existing_keys.add((sid, ts))
            inserted += 1

        # compaction 要否を同一 connection 内で判定（rebuild は close 後にファイル swap）。
        needs_compaction = _needs_compaction(con)
    except Exception:
        # 取り込み失敗時は rotate しない（jsonl を温存して次回再試行）。
        return 0
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    # ここに来た時点で db への取り込みは成功。jsonl を rotate する。
    _rotate_jsonl()

    # 保険 compaction: connection を閉じた後にファイル swap で rebuild する。
    if needs_compaction:
        _compact_db()
    return inserted


def _rotate_jsonl() -> None:
    """live jsonl を `.ingested-<ts>` へ rename し、古い rotate 済みを 1 世代に削る。"""
    try:
        if not SESSIONS_JSONL.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        rotated = DATA_DIR / f"sessions.jsonl.ingested-{ts}"
        SESSIONS_JSONL.rename(rotated)
        # 1世代保持: 最新の rotate 済み 1 件を残して残りを削除。
        all_rotated = sorted(DATA_DIR.glob(_ROTATED_GLOB))
        for old in all_rotated[:-1]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _needs_compaction(con) -> bool:
    """db ファイルサイズが (rows × 平均 raw_json 行長) の閾値倍を超えているか判定。

    free page（大量 INSERT→DELETE 後の残骸）でファイルが論理サイズから乖離した状態を検出。
    DuckDB はブロック単位割り当てで最小ファイルサイズの床があるため、床（既定 db サイズ）を
    超える分だけを乖離として評価する。
    """
    try:
        stats = con.execute(
            "SELECT COUNT(*), COALESCE(AVG(LENGTH(raw_json)), 0) FROM sessions"
        ).fetchone()
        rows = int(stats[0]) if stats else 0
        avg_len = float(stats[1]) if stats and stats[1] is not None else 0.0
        if rows == 0:
            return False
        try:
            file_size = SESSIONS_DB.stat().st_size
        except OSError:
            return False
        # 最小ファイルサイズの床近辺は縮める余地が無いので compaction しない。
        if file_size < _COMPACTION_MIN_BYTES:
            return False
        logical = rows * max(avg_len, 1.0)
        if logical <= 0:
            return False
        return file_size > logical * _COMPACTION_BLOAT_RATIO
    except Exception:
        return False


def _compact_db() -> None:
    """sessions.db を新規ファイルへ rebuild して swap し、free page を解放する。

    DuckDB は in-place の DROP/CREATE では割り当て済みブロックを返さないため、
    ATTACH した新規 db へ `CREATE TABLE AS` でコピー → 元ファイルを差し替える。
    connection は呼び出し前に閉じておくこと（ファイル swap のため）。
    """
    if not HAS_DUCKDB or not SESSIONS_DB.exists():
        return
    fresh = SESSIONS_DB.with_suffix(".db.compact")
    con = None
    try:
        # 既存の中途半端な compact ファイルを除去。
        for p in (fresh, Path(str(fresh) + ".wal")):
            if p.exists():
                p.unlink()
        con = _duckdb.connect(str(SESSIONS_DB))
        con.execute(f"ATTACH '{fresh}' AS fresh")
        con.execute("CREATE TABLE fresh.sessions AS SELECT * FROM sessions")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON fresh.sessions(timestamp)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON fresh.sessions(session_id)"
        )
        con.close()
        con = None
        # 元ファイルを新規ファイルで差し替え（WAL も掃除）。
        wal = Path(str(SESSIONS_DB) + ".wal")
        if wal.exists():
            wal.unlink()
        os.replace(str(fresh), str(SESSIONS_DB))
    except Exception:
        # compaction はベストエフォート。失敗時は元ファイルを温存する。
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        try:
            if fresh.exists():
                fresh.unlink()
        except OSError:
            pass


def _uningested_jsonl_records():
    """未 ingest（rotate されていない live）jsonl のレコードを yield。

    rotate 済み（`.ingested-*`）は ingest 後の重複なので除外する。
    """
    yield from _iter_jsonl_records(SESSIONS_JSONL)


def count_unique_since(timestamp: str) -> int:
    """timestamp より後のユニーク session_id 数を返す（union read）。

    db の結果 + 未 ingest jsonl の結果を (session_id, timestamp) で dedup して合算する。
    """
    pairs: set[tuple[str, str]] = set()

    if HAS_DUCKDB and SESSIONS_DB.exists():
        con = None
        try:
            con = _connect()
            rows = con.execute(
                "SELECT DISTINCT session_id, timestamp FROM sessions "
                "WHERE timestamp > ? AND session_id IS NOT NULL AND session_id != ''",
                [timestamp],
            ).fetchall()
            for sid, ts in rows:
                if sid:
                    pairs.add((sid, ts))
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    # 未 ingest jsonl を合算（dedup は (session_id, timestamp) で行う）。
    for rec in _uningested_jsonl_records():
        ts = rec.get("timestamp", "")
        sid = rec.get("session_id", "")
        if sid and ts > timestamp:
            pairs.add((sid, ts))

    # ユニーク session_id 数（同一 session_id が複数 timestamp を持つ場合は 1 とカウント）。
    return len({sid for sid, _ in pairs})


def query(since: str | None = None, limit: int | None = None) -> list[dict]:
    """セッションレコードを返す（union read）。

    db の結果 + 未 ingest jsonl の結果を (session_id, timestamp) で dedup して合算し、
    timestamp 昇順に並べて返す。limit は union 後に適用する。

    Args:
        since: ISO 8601 timestamp。指定時はこれより新しいレコードのみ。
        limit: 返す件数の上限（union 後に適用）。
    """
    # dedup キー → レコード。db を先に入れ、jsonl は未取り込み分のみ補完する。
    by_key: dict[tuple[str, str], dict] = {}

    if HAS_DUCKDB and SESSIONS_DB.exists():
        con = None
        try:
            con = _connect()
            sql = "SELECT raw_json FROM sessions"
            params: list[Any] = []
            if since:
                sql += " WHERE timestamp > ?"
                params.append(since)
            rows = con.execute(sql, params).fetchall()
            for (raw,) in rows:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                key = (rec.get("session_id", ""), rec.get("timestamp", ""))
                by_key[key] = rec
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    for rec in _uningested_jsonl_records():
        if since and rec.get("timestamp", "") <= since:
            continue
        key = (rec.get("session_id", ""), rec.get("timestamp", ""))
        # db に既にある (session_id, timestamp) は db を優先（dedup）。
        if key not in by_key:
            by_key[key] = rec

    results = sorted(by_key.values(), key=lambda r: r.get("timestamp", ""))
    if limit:
        results = results[: int(limit)]
    return results


def read_session_records(
    data_dir: Path | None = None, *, since: str | None = None
) -> list[dict]:
    """任意の data_dir の session レコードを union read する（読み取り専用・書込なし）。

    #469: outcome_metrics / outcome_promotion_readiness の session 系分母を実効化するため、
    sessions.jsonl 直読でなく **DuckDB sessions.db + 未 ingest live jsonl** の合算を返す。
    sessions.jsonl は #415 で db へ ingest 後 rotate されるため live jsonl はほぼ空であり、
    jsonl だけ読むと session 系の分母が構造的に常に空（永遠に ✗）になっていた。

    重複除去キーは ``ingest()`` と同一の ``(session_id, timestamp)``（db 優先）。
    duckdb が import できない / db が存在しない場合は jsonl のみへ graceful fallback する
    （既存 ``query`` / ``count_unique_since`` と同じ流儀）。

    **読み取り専用**: db は read_only 接続で開き、スキーマ作成（CREATE TABLE）も mkdir も
    行わない。dry-run の「1バイトも書かない」契約（#461）を壊さないため。

    Args:
        data_dir: 読む対象 dir。None ならモジュール定数 ``DATA_DIR``（= 既定の解決パス）。
                  outcome 系は tmp DATA_DIR を monkeypatch するため、その値をここへ渡す。
        since: ISO 8601 timestamp。指定時は ``timestamp > since`` のレコードのみ返す。
               None なら窓フィルタなし（呼び出し側が ``_in_window`` 等で別途窓判定する想定）。

    Returns:
        session レコード dict の list（timestamp 昇順）。
    """
    base = data_dir if data_dir is not None else DATA_DIR
    db_path = base / "sessions.db"
    jsonl_path = base / "sessions.jsonl"

    by_key: dict[tuple[str, str], dict] = {}

    if HAS_DUCKDB and db_path.exists():
        con = None
        try:
            # read_only=True: スキーマ作成・mkdir をせず、ファイルを書き換えない。
            con = _duckdb.connect(str(db_path), read_only=True)
            sql = "SELECT raw_json FROM sessions"
            params: list[Any] = []
            if since:
                sql += " WHERE timestamp > ?"
                params.append(since)
            rows = con.execute(sql, params).fetchall()
            for (raw,) in rows:
                try:
                    rec = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                key = (rec.get("session_id", ""), rec.get("timestamp", ""))
                by_key[key] = rec
        except Exception:
            # db が壊れている / table 不在 / read_only 不可 等は jsonl のみへ fallback。
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    for rec in _iter_jsonl_records(jsonl_path):
        if not isinstance(rec, dict):
            continue
        ts = rec.get("timestamp", "")
        if since and ts <= since:
            continue
        key = (rec.get("session_id", ""), ts)
        # db に既にある (session_id, timestamp) は db を優先（dedup）。
        if key not in by_key:
            by_key[key] = rec

    return sorted(by_key.values(), key=lambda r: r.get("timestamp", ""))


def migrate_from_jsonl(skip_if_db_has_data: bool = False) -> int:
    """sessions.jsonl のレコードを sessions.db に取り込む（後方互換 CLI 用）。

    べき等: 同じ (session_id, timestamp) ペアは重複挿入しない。
    `ingest()` と異なり jsonl の rotate は行わない（migrate_sessions_to_duckdb.py の
    薄い CLI ラッパーが想定する従来挙動を温存する）。

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

        existing_keys = {
            (row[0], row[1])
            for row in con.execute("SELECT session_id, timestamp FROM sessions").fetchall()
        }

        inserted = 0
        for rec in _iter_jsonl_records(SESSIONS_JSONL):
            sid = rec.get("session_id", "")
            ts = rec.get("timestamp", "")
            if not sid or not ts:
                continue
            if (sid, ts) in existing_keys:
                continue
            con.execute(
                "INSERT INTO sessions (session_id, timestamp, project, type, skill_count, error_count, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                _record_to_row(rec),
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

    db と未 ingest jsonl の両方から削除する（union 書き込み側の整合）。

    Args:
        session_ids: 削除対象の session_id リスト。
        source: 指定時はこの source を持つレコードのみ削除（backfill 等）。

    Returns:
        削除件数（db + jsonl の合算）。
    """
    if not session_ids:
        return 0

    deleted = 0
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
            deleted += len(rows)
        except Exception:
            pass
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

    deleted += _delete_by_session_ids_jsonl(session_ids, source=source)
    return deleted


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
