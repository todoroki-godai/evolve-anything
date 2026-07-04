"""store_schema_repair — DuckDB テーブルの PK/UNIQUE 制約欠落を検出し自己修復する（#156）。

背景（#156 / pitfall: CTAS が制約を落とす）: ``data_dir_migration.merge_db`` の
``CREATE TABLE {t} AS SELECT ...``（CTAS）rebuild は PRIMARY KEY / UNIQUE index を
引き継がない。結果、``token_usage.db`` / ``utterances.db`` が constraints=[] になり、
``INSERT OR IGNORE`` / ``ON CONFLICT DO NOTHING`` が
``BinderException: There are no UNIQUE/PRIMARY KEY constraints`` で全停止する
（writer 全死）。

本モジュールは write 経路の connect 時（token_usage_store._connect /
utterance_archive.store.connection）と migration の rebuild 直後に呼ばれ、
制約欠落を ``duckdb_constraints()`` / ``duckdb_indexes()`` で決定論検出し、
キー列で dedup → canonical ``_SCHEMA_SQL`` で再作成 → INSERT → swap して修復する。

修復方式（DROP dance・index 名衝突回避）:
1. 現テーブルを一時テーブル ``{t}__schema_repair_src`` へ CTAS コピー（index を持たない）
2. 破損テーブルを DROP（付随 index 名を解放）
3. canonical ``schema_sql`` を実行（PK + index を復元・**単一ソース**。sibling table は
   ``IF NOT EXISTS`` で無害な no-op）
4. dedup 済み行を ``INSERT``（キーグループ順に row_number で決定論 dedup）
5. 一時テーブルを DROP
全体を 1 トランザクションで囲み、失敗時は ROLLBACK して現状維持（no-op で writer は
従来通り＝退行なし）。

**read_only 経路には呼ばれない**（dry-run byte 契約 #65）。連結する store 側が
read パスでは ``repair=False`` を渡す / read_only 接続からは呼ばない。
決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from typing import Any, List, Sequence, Tuple


def _q(name: str) -> str:
    """DuckDB 識別子を二重引用符でクオートする（埋め込み " はエスケープ）。"""
    return '"' + name.replace('"', '""') + '"'


def _table_exists(con: Any, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = ?", [table]
    ).fetchone()
    return bool(row and row[0])


def _has_primary_key(con: Any, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
        [table],
    ).fetchone()
    return bool(row and row[0])


def _has_unique_index(con: Any, table: str) -> bool:
    """``CREATE UNIQUE INDEX`` は duckdb_constraints() に出ず duckdb_indexes() の
    ``is_unique = TRUE`` に現れる。utterances のように PK + 論理 UNIQUE index の 2 本を
    要求するストア向けに「UNIQUE index が 1 つ以上あるか」を見る。"""
    row = con.execute(
        "SELECT COUNT(*) FROM duckdb_indexes() "
        "WHERE table_name = ? AND is_unique = TRUE",
        [table],
    ).fetchone()
    return bool(row and row[0])


def needs_repair(con: Any, table: str, *, require_unique_index: bool = False) -> bool:
    """テーブルが存在するのに期待する PK（+ 任意で UNIQUE index）が欠落しているか。

    テーブル自体が無い場合は「これから schema_sql が正しく作る」ため False。
    """
    if con is None:
        return False
    try:
        if not _table_exists(con, table):
            return False
        if not _has_primary_key(con, table):
            return True
        if require_unique_index and not _has_unique_index(con, table):
            return True
    except Exception:
        return False
    return False


def _table_columns(con: Any, table: str) -> List[str]:
    rows = con.execute(
        "SELECT column_name FROM duckdb_columns() "
        "WHERE table_name = ? ORDER BY column_index",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def _dedup_insert_sql(
    table: str, src: str, cols: Sequence[str], dedup_keys: Sequence[Sequence[str]]
) -> str:
    """``src`` からキーグループ順に決定論 dedup して ``table`` へ INSERT する SQL。

    各 dedup_keys グループごとに ``row_number() OVER (PARTITION BY keys ORDER BY 全列)``
    で 1 行だけ残す（全列 ORDER BY なので同キー内でも決定論。完全同一行はどれを残しても
    等価）。グループは前から順に適用（例: utterances は 物理 PK → 論理 UNIQUE）。
    """
    collist = ", ".join(_q(c) for c in cols)
    order_by = collist  # 全列順 = 決定論
    inner = f"(SELECT {collist} FROM {_q(src)})"
    for keys in dedup_keys:
        part = ", ".join(_q(k) for k in keys)
        inner = (
            f"(SELECT {collist} FROM (SELECT {collist}, "
            f"row_number() OVER (PARTITION BY {part} ORDER BY {order_by}) AS _rn "
            f"FROM {inner}) WHERE _rn = 1)"
        )
    return f"INSERT INTO {_q(table)} ({collist}) SELECT {collist} FROM {inner}"


def repair_table(
    con: Any,
    table: str,
    schema_sql: str,
    dedup_keys: Sequence[Sequence[str]],
    *,
    require_unique_index: bool = False,
    label: str | None = None,
) -> bool:
    """``table`` の PK/UNIQUE 制約が欠落していれば dedup + rebuild で復元する。

    Args:
        con: write 可能な DuckDB 接続（read_only 接続を渡してはならない）。
        table: 修復対象テーブル名。
        schema_sql: 当該テーブルを含む canonical ``_SCHEMA_SQL``（store から import・単一ソース）。
        dedup_keys: 重複排除キーのグループ列（優先順）。例: [("uuid",)] /
            [("source_path","line_no"), ("session_id","timestamp","text_hash")]。
        require_unique_index: PK に加えて UNIQUE index も必須なら True（utterances）。
        label: surface 用の表示名（省略時は table）。

    Returns:
        修復を実行したら True、健全で no-op なら False。失敗時も False（ROLLBACK 済み）。
    """
    if not needs_repair(con, table, require_unique_index=require_unique_index):
        return False
    src = f"{table}__schema_repair_src"
    cols = _table_columns(con, table)
    dedup_insert = _dedup_insert_sql(table, src, cols, dedup_keys)
    try:
        con.execute("BEGIN")
        con.execute(f"DROP TABLE IF EXISTS {_q(src)}")
        con.execute(f"CREATE TABLE {_q(src)} AS SELECT * FROM {_q(table)}")
        con.execute(f"DROP TABLE {_q(table)}")
        con.execute(schema_sql)
        con.execute(dedup_insert)
        con.execute(f"DROP TABLE {_q(src)}")
        con.execute("COMMIT")
    except Exception as e:  # 失敗は現状維持（退行なし）で surface のみ
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        print(
            f"[evolve-anything:schema-repair] {label or table}: 修復失敗 ({e}) — 現状維持",
            file=sys.stderr,
        )
        return False
    print(
        f"[evolve-anything:schema-repair] {label or table}: "
        f"PK/UNIQUE 制約を復元しました（dedup + rebuild）",
        file=sys.stderr,
    )
    return True


# ── 既知ストアのテーブル → (dedup_keys, require_unique_index) レジストリ ─────────────
# schema_sql は store モジュールから遅延 import（コピー禁止・単一ソース）。migration の
# rebuild 直後に既知ストアの制約を復元するために data_dir_migration が参照する。
def known_store_specs() -> "list[Tuple[str, str, list, bool]]":
    """[(table, schema_sql, dedup_keys, require_unique_index), ...] を返す。

    store モジュールの import 失敗（DuckDB 無し等）は空スキップ。
    """
    specs: "list[Tuple[str, str, list, bool]]" = []
    try:
        import token_usage_store as _tus  # type: ignore

        specs.append(("token_usage", _tus._SCHEMA_SQL, [("uuid",)], False))
        specs.append(
            ("session_progress", _tus._SCHEMA_SQL, [("pj_id", "session_id")], False)
        )
    except Exception:
        pass
    try:
        from utterance_archive import store as _ustore  # type: ignore

        specs.append(
            (
                "utterances",
                _ustore._SCHEMA_SQL,
                [("source_path", "line_no"), ("session_id", "timestamp", "text_hash")],
                True,
            )
        )
        specs.append(
            ("ingest_state", _ustore._SCHEMA_SQL, [("source_path",)], False)
        )
    except Exception:
        pass
    return specs


def repair_known_tables(con: Any, present_tables: "set[str] | None" = None) -> List[str]:
    """接続内の既知ストアテーブルのうち制約欠落しているものを一括修復する。

    migration の merge_db が CTAS で制約を落とした直後に呼ぶ想定。
    Returns: 実際に修復したテーブル名のリスト。
    """
    repaired: List[str] = []
    for table, schema_sql, dedup_keys, req_uidx in known_store_specs():
        if present_tables is not None and table not in present_tables:
            continue
        try:
            if repair_table(
                con, table, schema_sql, dedup_keys, require_unique_index=req_uidx
            ):
                repaired.append(table)
        except Exception:
            continue
    return repaired
