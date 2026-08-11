"""utterance_archive.query のテスト（#430）。

query API 契約: pj_slug 必須 / source_kind デフォルト dialogue / since / 横断別関数。
書き込み先は tmp_path のみ。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from utterance_archive import query as uquery  # noqa: E402
from utterance_archive import store as ustore  # noqa: E402
from utterance_archive.extractor import Utterance  # noqa: E402

pytestmark = pytest.mark.skipif(not ustore.HAS_DUCKDB, reason="DuckDB 未インストール")


def _seed(db: Path) -> None:
    rows = [
        Utterance("/p/a.jsonl", 1, "evolve-anything", "s1", "2026-05-01T00:00:00Z",
                  "古い対話", "h1", None, "dialogue", 1),
        Utterance("/p/a.jsonl", 2, "evolve-anything", "s1", "2026-06-09T00:00:00Z",
                  "新しい対話", "h2", None, "dialogue", 1),
        Utterance("/p/a.jsonl", 3, "evolve-anything", "s1", "2026-06-09T00:01:00Z",
                  "長文ペースト", "h3", None, "long_paste", 1),
        Utterance("/p/b.jsonl", 1, "otherpj", "s2", "2026-06-09T00:00:00Z",
                  "別PJの発話", "h4", None, "dialogue", 1),
    ]
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, rows)


def test_pj_slug_required() -> None:
    with pytest.raises(ValueError):
        uquery.query_utterances("")


def test_filters_by_pj_slug(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    rows = uquery.query_utterances("evolve-anything", db_path=db)
    assert {r["text"] for r in rows} == {"古い対話", "新しい対話"}  # long_paste 除外, 別PJ除外


def test_default_source_kind_is_dialogue_only(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    rows = uquery.query_utterances("evolve-anything", db_path=db)
    assert all(r["source_kind"] == "dialogue" for r in rows)


def test_opt_in_long_paste(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    rows = uquery.query_utterances(
        "evolve-anything", source_kinds=("dialogue", "long_paste"), db_path=db
    )
    assert any(r["source_kind"] == "long_paste" for r in rows)


def test_since_returns_old_and_new(tmp_path: Path) -> None:
    """14日より古い発話も since=None で取得できる（PR2 完了定義）。"""
    db = tmp_path / "u.db"
    _seed(db)
    all_rows = uquery.query_utterances("evolve-anything", db_path=db)
    assert any(r["text"] == "古い対話" for r in all_rows)
    # since で絞ると古いものは落ちる
    recent = uquery.query_utterances("evolve-anything", since="2026-06-01T00:00:00Z", db_path=db)
    assert all(r["text"] != "古い対話" for r in recent)


def test_keyword_filter(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    rows = uquery.query_utterances("evolve-anything", keyword="新しい", db_path=db)
    assert len(rows) == 1
    assert rows[0]["text"] == "新しい対話"


def test_all_projects_crosses_pj(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    rows = uquery.query_utterances_all_projects(db_path=db)
    slugs = {r["pj_slug"] for r in rows}
    assert "evolve-anything" in slugs and "otherpj" in slugs


def test_query_missing_db_returns_empty(tmp_path: Path) -> None:
    rows = uquery.query_utterances("evolve-anything", db_path=tmp_path / "nope.db")
    assert rows == []


# ── #410 round3 [Must]3 / pitfall_duckdb_read_opens_readwrite（#65）再発防止 ──────
# 旧実装は read 経路（query_utterances / query_utterances_all_projects）が
# ``_store.connection(db_path, repair=False)`` 経由で開いていたが、``repair=False`` は
# 自己修復（DDL の追加実行）を止めるだけで、接続自体は read-write のままだった
# （``_duckdb.connect(str(db_path))`` に ``read_only`` kwarg が無い）。DB が既に存在する
# 状態で読むだけでも ``con.execute(_SCHEMA_SQL)``（CREATE TABLE IF NOT EXISTS 等）が
# write transaction として走りファイル byte を書き換える（dry-run byte 契約違反・
# judge_runner の dry-run 実本番経路 utterances=None → query_utterances_all_projects
# 経由で発火する）。db を chmod 444 にすると read-write open は失敗するが read_only open
# は成功する — この違いで判別する（episodic_store.test_read_does_not_require_write_access
# と同型の判別法）。


def test_query_utterances_read_only_does_not_require_write_access(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    db.chmod(0o444)
    try:
        rows = uquery.query_utterances("evolve-anything", db_path=db)
        assert len(rows) >= 1, "read_only でない（444 db を読めず例外/空返し＝write 権限要求）"
    finally:
        db.chmod(0o644)


def test_query_utterances_all_projects_read_only_does_not_require_write_access(tmp_path: Path) -> None:
    db = tmp_path / "u.db"
    _seed(db)
    db.chmod(0o444)
    try:
        rows = uquery.query_utterances_all_projects(db_path=db)
        assert len(rows) >= 1, "read_only でない（444 db を読めず例外/空返し＝write 権限要求）"
    finally:
        db.chmod(0o644)
