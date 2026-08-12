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


def test_quoted_marker_in_question_is_not_deleted(tmp_path):
    """機構マーカーを引用しつつ質問する人間の発話は削除しない（Must1・codex PR #377）。

    is_machinery_prompt は先頭300文字以内に marker を「含む」だけで判定するため、
    人間が機構出力を引用して質問する発話（marker が文頭でない）を誤検出していた。
    """
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [_row(1, "evolve-anything", "「Stop hook feedback:」という出力はどこで生成されますか？")],
    )

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0


def test_machinery_output_quoted_in_code_block_is_not_deleted(tmp_path):
    """機構出力をコードブロックに引用して解析依頼する発話は削除しない（Must1）。"""
    db = tmp_path / "utterances.db"
    text = "このログを解析して:\n```\nStop hook feedback:\n先送り表現を検出しました\n```"
    _seed(db, [_row(1, "evolve-anything", text)])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0


def test_skill_md_fragment_quote_is_not_deleted(tmp_path):
    """SKILL.md断片（Base directory for this skill: を含む）を引用した質問は削除しない（Must1）。"""
    db = tmp_path / "utterances.db"
    text = "SKILL.mdのこの断片を説明して:\nBase directory for this skill: /path/to/skill"
    _seed(db, [_row(1, "evolve-anything", text)])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0


def test_question_about_system_reminder_tag_is_not_deleted(tmp_path):
    """<system-reminder> タグそのものについて質問する発話は削除しない（Must1）。

    is_machinery_prompt の prefix 判定は startswith のみなので、人間が
    <system-reminder> を書き出しに引用してから質問する発話も誤って先頭一致してしまう。
    purge では末尾が疑問符（引用+質問のシグナル）の場合は追加で除外する。
    """
    db = tmp_path / "utterances.db"
    text = "<system-reminder>この中身は何ですか？</system-reminder> は何ですか？"
    _seed(db, [_row(1, "evolve-anything", text)])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 0
    assert result["deleted_count"] == 0


def test_system_reminder_prefix_without_trailing_question_is_still_deleted(tmp_path):
    """疑問符で終わらない正規の system-reminder 注入は引き続き削除対象（回帰防止）。"""
    db = tmp_path / "utterances.db"
    text = "<system-reminder>harness が注入した本物のリマインダー本文です。</system-reminder>"
    _seed(db, [_row(1, "evolve-anything", text)])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 1
    assert result["deleted_count"] == 1


def test_apply_is_transactional_all_or_nothing_on_failure(tmp_path, monkeypatch):
    """削除処理の途中で例外が起きたら1行も削除されず全件ロールバックする（Must2）。"""
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [
            _row(1, "evolve-anything", "Stop hook feedback:\n1件目"),
            _row(2, "evolve-anything", "Stop hook feedback:\n2件目"),
            _row(3, "evolve-anything", "これは普通のユーザー発話です"),
        ],
    )

    real_delete = up._delete_confirmed_row
    call_count = {"n": 0}

    def _flaky_delete(con, row):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on 2nd delete")
        return real_delete(con, row)

    monkeypatch.setattr(up, "_delete_confirmed_row", _flaky_delete)

    with pytest.raises(RuntimeError):
        up.purge_machinery_utterances(db, apply=True)

    with ustore.connection(db, repair=False) as con:
        remaining = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    assert remaining == 3  # 1件目が消えた後に失敗しても全件ロールバックされている


def test_apply_skips_row_if_content_changed_since_detection(tmp_path, monkeypatch):
    """detection後にDBが変化（text_hash不一致）した行は削除せずスキップする（Should3）。

    検出（read_only接続）と削除（write接続）が別 snapshot のため、間に同一PKの
    行が別内容へ置換されると検出時と異なる内容を削除しうる。text_hash を
    削除直前に再検証することで防ぐ。
    """
    db = tmp_path / "utterances.db"
    _seed(db, [_row(1, "evolve-anything", "Stop hook feedback:\n本物の機構出力")])

    real_matched = up.find_machinery_rows(db)
    assert len(real_matched) == 1
    stale_row = dict(real_matched[0])
    stale_row["text_hash"] = "stale-hash-does-not-match-current-row"
    monkeypatch.setattr(up, "find_machinery_rows", lambda _db: [stale_row])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 1
    assert result["deleted_count"] == 0  # 再検証で不一致→削除されない
    with ustore.connection(db, repair=False) as con:
        remaining = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    assert remaining == 1


def test_apply_skips_row_already_deleted_since_detection(tmp_path, monkeypatch):
    """detection後に別経路で既に削除された行（PK不在）は例外を投げずスキップする（Should3）。"""
    db = tmp_path / "utterances.db"
    _seed(db, [_row(1, "evolve-anything", "Stop hook feedback:\n本物の機構出力")])

    phantom_row = {
        "source_path": "/does/not/exist/a.jsonl",
        "line_no": 999,
        "pj_slug": "evolve-anything",
        "session_id": "s-phantom",
        "timestamp": "2026-08-01T00:00:00Z",
        "text": "Stop hook feedback:\nphantom",
        "text_hash": "whatever",
    }
    monkeypatch.setattr(up, "find_machinery_rows", lambda _db: [phantom_row])

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 1
    assert result["deleted_count"] == 0
    with ustore.connection(db, repair=False) as con:
        remaining = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
    assert remaining == 1


def test_deleted_count_reflects_actual_deletion_not_candidate_count(tmp_path):
    """deleted_count は実際に削除された件数（候補数と乖離しうる）を返す（Should3）。"""
    db = tmp_path / "utterances.db"
    _seed(
        db,
        [
            _row(1, "evolve-anything", "Stop hook feedback:\n1件目"),
            _row(2, "evolve-anything", "Stop hook feedback:\n2件目"),
        ],
    )

    result = up.purge_machinery_utterances(db, apply=True)

    assert result["matched_count"] == 2
    assert result["deleted_count"] == 2  # 通常経路では候補数と一致することも確認


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
