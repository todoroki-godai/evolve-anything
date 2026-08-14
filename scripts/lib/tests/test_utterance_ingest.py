"""utterance_archive.ingest のテスト（#430）。

合成 fixture + 実機 1 PJ E2E（evolve-anything 自身の transcript）。
書き込み先は tmp_path のみ（実 DATA_DIR に触れない）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from utterance_archive import ingest as uingest  # noqa: E402
from utterance_archive import query as uquery  # noqa: E402
from utterance_archive import store as ustore  # noqa: E402

pytestmark = pytest.mark.skipif(not ustore.HAS_DUCKDB, reason="DuckDB 未インストール")


# 実 transcript と同様、各行に cwd を持たせる（pj_slug は cwd 由来で確定する）。
_CWD = "/Users/x/tools/evolve-anything"


def _user_line(text, ts, sid, uuid, cwd=_CWD):
    obj = {
        "type": "user", "uuid": uuid, "sessionId": sid, "timestamp": ts,
        "message": {"role": "user", "content": text},
    }
    if cwd is not None:
        obj["cwd"] = cwd
    return json.dumps(obj)


def _make_projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    pj = root / "-Users-x-tools-evolve-anything"
    pj.mkdir(parents=True)
    (pj / "s1.jsonl").write_text(
        _user_line("最初の発話", "2026-06-01T00:00:00Z", "s1", "u1") + "\n"
        + _user_line("二番目の発話", "2026-06-01T00:01:00Z", "s1", "u2") + "\n",
        encoding="utf-8",
    )
    return root


def test_ingest_extracts_human_only(tmp_path: Path) -> None:
    root = _make_projects_root(tmp_path)
    db = tmp_path / "utterances.db"
    res = uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    assert res["inserted"] == 2
    rows = uquery.query_utterances("evolve-anything", db_path=db)
    assert len(rows) == 2
    assert {r["text"] for r in rows} == {"最初の発話", "二番目の発話"}


# --- #445: [Image #N] プレースホルダの stats を ingest 経由でも surface ------------


def _make_image_projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    pj = root / "-Users-x-tools-evolve-anything"
    pj.mkdir(parents=True)
    (pj / "s1.jsonl").write_text(
        _user_line("[Image #1] Codeタブってないよ", "2026-06-01T00:00:00Z", "s1", "u1") + "\n"
        + _user_line("[Image #2]", "2026-06-01T00:01:00Z", "s1", "u2") + "\n"
        + _user_line("普通の発話", "2026-06-01T00:02:00Z", "s1", "u3") + "\n",
        encoding="utf-8",
    )
    return root


def test_ingest_pj_dir_surfaces_image_placeholder_stats(tmp_path: Path) -> None:
    root = _make_image_projects_root(tmp_path)
    pj_dir = root / "-Users-x-tools-evolve-anything"
    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        state = ustore.get_ingest_state(con)
        res = uingest.ingest_pj_dir(pj_dir, con, state, progress=False)
    # 2件抽出（bare な画像添付1件は除外）。
    assert res["inserted"] == 2
    assert res["image_placeholder_stripped"] == 1
    assert res["image_placeholder_only_excluded"] == 1


def test_ingest_all_projects_aggregates_image_placeholder_stats(tmp_path: Path) -> None:
    root = _make_image_projects_root(tmp_path)
    db = tmp_path / "utterances.db"
    res = uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    assert res["image_placeholder_stripped"] == 1
    assert res["image_placeholder_only_excluded"] == 1


def test_ingest_idempotent(tmp_path: Path) -> None:
    root = _make_projects_root(tmp_path)
    db = tmp_path / "utterances.db"
    uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    res2 = uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    assert res2["inserted"] == 0  # 増分 ingest: 変化なし


def test_ingest_incremental_appended_lines(tmp_path: Path) -> None:
    root = _make_projects_root(tmp_path)
    db = tmp_path / "utterances.db"
    uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    # 追記
    pj = root / "-Users-x-tools-evolve-anything"
    f = pj / "s1.jsonl"
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(_user_line("追記の発話", "2026-06-01T00:02:00Z", "s1", "u3") + "\n")
    # mtime を確実に進める
    import os
    os.utime(f, (time.time() + 10, time.time() + 10))
    res2 = uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    assert res2["inserted"] == 1


def test_ingest_writes_staleness_marker(tmp_path: Path) -> None:
    root = _make_projects_root(tmp_path)
    db = tmp_path / "utterances.db"
    uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    assert ustore.read_last_ingest_at(tmp_path) is not None
    assert ustore.is_stale(tmp_path, threshold_days=14) is False


def test_ingest_skips_subagents_files(tmp_path: Path) -> None:
    """`*/subagents/*.jsonl`（sidechain 専用ファイル）は走査候補から除外される
    （#379 ADR-054 A1。実データ検証で main-level transcript には isSidechain:true が
    現れず、subagents/ 側は 100% true と確認済み）。
    """
    root = _make_projects_root(tmp_path)
    pj = root / "-Users-x-tools-evolve-anything"
    sub = pj / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-xyz.jsonl").write_text(
        json.dumps(
            {
                "type": "user", "uuid": "su1", "sessionId": "ssub",
                "timestamp": "2026-06-01T00:03:00Z", "isSidechain": True,
                "cwd": _CWD,
                "message": {"role": "user", "content": "サブエージェント内部プロンプト"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    db = tmp_path / "utterances.db"
    res = uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    # subagents/ 配下のファイルはファイル数にも insert 件数にも寄与しない。
    assert res["inserted"] == 2  # s1.jsonl の2件のみ（subagents ファイルは除外）
    assert res["files_processed"] == 1  # s1.jsonl のみ処理（subagents ファイルは候補外）
    rows = uquery.query_utterances("evolve-anything", db_path=db)
    assert {r["text"] for r in rows} == {"最初の発話", "二番目の発話"}


def test_resume_duplicate_no_violation(tmp_path: Path) -> None:
    """同 session_id が複数ファイルに分かれ同一発話が複製されても重複ゼロ・例外なし。"""
    root = tmp_path / "projects"
    pj = root / "-Users-x-tools-evolve-anything"
    pj.mkdir(parents=True)
    line = _user_line("再開で複製される発話", "2026-06-01T00:00:00Z", "sresume", "uA")
    # resume された 2 ファイル: 同 session_id・同 timestamp・同 text
    (pj / "part1.jsonl").write_text(line + "\n", encoding="utf-8")
    (pj / "part2.jsonl").write_text(line + "\n", encoding="utf-8")
    db = tmp_path / "utterances.db"
    res = uingest.ingest_all_projects(projects_root=root, db_path=db, progress=False)
    # 物理キーは別（別ファイル）だが論理 UNIQUE が 1 件に収斂
    rows = uquery.query_utterances("evolve-anything", db_path=db)
    assert len(rows) == 1
    assert res["inserted"] == 1


# --- 実機 1 PJ E2E（transcript-store-bench ルール）-----------------------------
#
# 実 transcript を読む実機 E2E（旧 test_real_pj_e2e, 直列 35 秒）は #496 で
# 通し評価ゲート（bin/evolve-dogfood-gate）の Layer 1 へ移設した。検証内容
# （wall time / DB size / row 数）は scripts/lib/dogfood/ingest_check.py の
# check_ingest_e2e / check_real_pj_ingest に書き直して維持している。pytest
# スイートからは外し、ゲートの実機 1 周（evolve-dogfood-gate --layer 1）で確認する。
