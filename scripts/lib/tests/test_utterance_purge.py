"""utterance_purge.purge_machinery_utterances のテスト（#369）。

utterances.db の source_kind='dialogue' 行に混入した Stop hook 自己出力等の
機構ターン（#323/#336 の追随漏れで既存行に残存）を検出・除去する one-shot
purge ツール。判定は既存の rl_common.detection.is_machinery_prompt を再利用する
（独自マーカーリストを持たない）。すべて合成 DB（tmp_path）で検証し、実 DB には
一切触れない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from utterance_archive import store as ustore
from utterance_archive.extractor import Utterance

import utterance_purge as up

pytestmark = pytest.mark.skipif(not ustore.HAS_DUCKDB, reason="DuckDB 未インストール")


def _seed(db: Path, rows) -> None:
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, rows)


def _row(
    line_no: int,
    pj_slug: str,
    text: str,
    source_kind: str = "dialogue",
    session_id: str = "s1",
    timestamp: str = "2026-08-01T00:00:00Z",
) -> Utterance:
    return Utterance(
        source_path="/p/a.jsonl",
        line_no=line_no,
        pj_slug=pj_slug,
        session_id=session_id,
        timestamp=timestamp,
        text=text,
        text_hash=f"h{line_no}",
        prev_action=None,
        source_kind=source_kind,
        extractor_version=2,
    )


def test_dry_run_detects_but_writes_nothing(tmp_path):
    """dry-run（既定）: 機構ターンを検出するが DB には一切書き込まない。"""
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [
            _row(1, "evolve-anything", "Stop hook feedback:\n先送り表現を検出しました"),
            _row(2, "evolve-anything", "これは普通のユーザー発話です"),
        ],
    )
    before = db.read_bytes()

    result = up.purge_machinery_utterances(db)

    assert result["dry_run"] is True
    assert result["matched_count"] == 1
    assert result["by_pj"] == {"evolve-anything": 1}
    assert result["deleted_count"] == 0
    assert db.read_bytes() == before  # 書込ゼロ


def test_apply_deletes_only_matched_rows(tmp_path):
    """--apply 相当（apply=True）: 機構ターンのみ削除し、正当な発話は残す。"""
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [
            _row(1, "evolve-anything", "Stop hook feedback:\n先送り表現を検出しました"),
            _row(2, "evolve-anything", "これは普通のユーザー発話です"),
        ],
    )

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["dry_run"] is False
    assert result["matched_count"] == 1
    assert result["deleted_count"] == 1

    with ustore.connection(db, repair=False) as con:
        remaining = con.execute("SELECT text FROM utterances ORDER BY line_no").fetchall()
    assert remaining == [("これは普通のユーザー発話です",)]


def test_clean_db_reports_zero_matches(tmp_path):
    """機構ターンが無い DB は matched_count=0、apply しても何も消えない。"""
    db = tmp_path / "utterances.db"
    _seed(db, [_row(1, "evolve-anything", "これは普通のユーザー発話です")])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0
    assert result["by_pj"] == {}


def test_only_dialogue_source_kind_is_scanned(tmp_path):
    """source_kind != 'dialogue' の行は対象外（long_paste 等は判定しない）。"""
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [
            _row(1, "evolve-anything", "Stop hook feedback:\n長文の機構出力", source_kind="long_paste"),
        ],
    )

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0


def test_missing_db_returns_empty_result(tmp_path):
    """DB ファイルが存在しない場合は例外を投げず空結果を返す。"""
    db = tmp_path / "does-not-exist.db"

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0
    assert result["by_pj"] == {}


def test_by_pj_breakdown_across_multiple_projects(tmp_path):
    """複数 PJ にまたがる混入を PJ 別に集計する。"""
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [
            _row(1, "pj-a", "Stop hook feedback:\nA", session_id="sa"),
            _row(2, "pj-a", "Stop hook feedback:\nA2", session_id="sa2"),
            _row(3, "pj-b", "Stop hook feedback:\nB", session_id="sb"),
            _row(4, "pj-b", "正当な発話", session_id="sb2"),
        ],
    )

    result = up.purge_machinery_utterances(db)

    assert result["by_pj"] == {"pj-a": 2, "pj-b": 1}
    assert result["matched_count"] == 3


def test_sample_is_truncated_to_sample_size(tmp_path):
    """代表サンプルは sample_size 件までに切り詰められる。"""
    db = tmp_path / "utterances.db"
    rows = [
        _row(i, "evolve-anything", f"Stop hook feedback:\n{i}", session_id=f"s{i}")
        for i in range(1, 4)
    ]
    _seed(db, rows)

    result = up.purge_machinery_utterances(db, sample_size=2)

    assert result["matched_count"] == 3
    assert len(result["sample"]) == 2


def test_dry_run_works_on_read_only_db(tmp_path):
    """dry-run の検出は read_only 接続を使うため、DB が read-only（chmod 444）でも動く。

    pitfall_duckdb_read_opens_readwrite の回帰テスト: 通常の write-capable connect は
    read-only ファイルへの初回 open で EACCES になる。read_only=True であれば成功する。
    """
    db = tmp_path / "utterances.db"
    _seed(db, [_row(1, "evolve-anything", "Stop hook feedback:\n先送り表現を検出しました")])
    db.chmod(0o444)
    try:
        result = up.purge_machinery_utterances(db)
        assert result["matched_count"] == 1
        assert result["dry_run"] is True
    finally:
        db.chmod(0o644)
