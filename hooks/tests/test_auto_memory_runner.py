"""auto_memory_runner.py のユニットテスト（[ADR-037] Phase 2）。

hook は corrections を生成前ゲートして PJ スコープキューに enqueue するだけのゼロ LLM 化。
LLM 生成・belief ゲート・memory 書き込みは drain（auto_memory_broker）が担う。
すべてのテストは LLM を呼ばない（subprocess.run の mock も不要）。
"""
import json
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
_LIB = _HOOKS.parent / "scripts" / "lib"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_LIB))

import auto_memory_runner
import auto_memory_broker


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """一時的なメモリディレクトリ。"""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    return mem_dir


@pytest.fixture
def tmp_data_dir(tmp_path):
    """一時データディレクトリ。"""
    data_dir = tmp_path / "evolve-anything"
    data_dir.mkdir()
    return data_dir


def _write_corrections(data_dir: Path, count: int = 5) -> None:
    """テスト用 corrections.jsonl を書き出す。"""
    corrections_file = data_dir / "corrections.jsonl"
    for i in range(count):
        record = {
            "session_id": f"sess-{i:04d}",
            "timestamp": f"2026-05-25T10:0{i}:00Z",
            "original": f"original text {i}",
            "corrected": f"corrected text {i}",
        }
        with corrections_file.open("a") as f:
            f.write(json.dumps(record) + "\n")


# ─── Test 1: 正常系 enqueue ─────────────────────────────────────────────────


def test_normal_enqueues_record_no_md_written(tmp_data_dir, tmp_memory_dir):
    """正常系: corrections あり → キューに record 1件、.md は生成されない。"""
    _write_corrections(tmp_data_dir, count=5)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)

    records = auto_memory_broker.read_queue("testslug", tmp_data_dir)
    assert len(records) == 1
    assert records[0]["slug"] == "testslug"
    assert len(records[0]["corrections"]) == 5

    # hook は memory を一切書かない
    assert list(tmp_memory_dir.glob("auto_*.md")) == []


# ─── Test 2: 並行 run() でキューが壊れない ───────────────────────────────────


def test_concurrent_runs_queue_intact(tmp_data_dir, tmp_memory_dir):
    """並行起動シミュレーション: 2 スレッドが同時に enqueue してもキューが壊れない。"""
    _write_corrections(tmp_data_dir, count=5)

    errors = []

    def worker():
        try:
            auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)
        except Exception as e:
            errors.append(e)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert errors == [], f"Unexpected errors: {errors}"
    # 同一 corrections 窓 → dedup → record は1件に collapse する
    records = auto_memory_broker.read_queue("testslug", tmp_data_dir)
    assert len(records) == 1


# ─── Test 3: dedup ──────────────────────────────────────────────────────────


def test_dedup_same_corrections_one_record(tmp_data_dir, tmp_memory_dir):
    """同一 corrections で run() 2回 → キュー record は1件。"""
    _write_corrections(tmp_data_dir, count=5)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)

    records = auto_memory_broker.read_queue("testslug", tmp_data_dir)
    assert len(records) == 1


# ─── Test 4: corrections 不在/空 → graceful exit ───────────────────────────


def test_missing_corrections_exits_gracefully(tmp_data_dir, tmp_memory_dir):
    """corrections.jsonl が存在しない場合は例外を吐かずに終了、キュー空。"""
    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)

    assert auto_memory_broker.read_queue("testslug", tmp_data_dir) == []
    assert list(tmp_memory_dir.glob("auto_*.md")) == []


def test_empty_corrections_exits_gracefully(tmp_data_dir, tmp_memory_dir):
    """corrections.jsonl が空の場合も例外を吐かずに終了、キュー空。"""
    (tmp_data_dir / "corrections.jsonl").write_text("")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)

    assert auto_memory_broker.read_queue("testslug", tmp_data_dir) == []


# ─── Test 5: memory_gating で全件落ち → キュー空 ────────────────────────────


def test_gating_filters_all_queue_empty(tmp_data_dir, tmp_memory_dir):
    """生成前ゲートで全件落ちたらキューは空のまま。"""
    _write_corrections(tmp_data_dir, count=3)

    # _score_correction が should_store=False を返すよう mock
    blocked = mock.MagicMock()
    blocked.should_store = False

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("auto_memory_runner._HAS_MEMORY_GATING", True), \
         mock.patch("auto_memory_runner._score_correction", return_value=blocked), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "0"}):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)

    assert auto_memory_broker.read_queue("testslug", tmp_data_dir) == []


def test_gating_keeps_survivors(tmp_data_dir, tmp_memory_dir):
    """生成前ゲートで一部生き残ったら生き残りのみ enqueue される。"""
    _write_corrections(tmp_data_dir, count=3)

    kept = mock.MagicMock()
    kept.should_store = True

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch("auto_memory_runner._HAS_MEMORY_GATING", True), \
         mock.patch("auto_memory_runner._score_correction", return_value=kept), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "0"}):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="testslug", memory_dir=tmp_memory_dir)

    records = auto_memory_broker.read_queue("testslug", tmp_data_dir)
    assert len(records) == 1
    assert len(records[0]["corrections"]) == 3


# ─── Test 6: slug 解決 (CLAUDE_PROJECT_DIR) ─────────────────────────────────


def test_slug_resolved_from_project_dir(tmp_data_dir, tmp_path):
    """slug 未指定時は CLAUDE_PROJECT_DIR の basename を slug にする。"""
    _write_corrections(tmp_data_dir, count=3)
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch.dict("os.environ", {
             "RL_GATING_DISABLED": "1",
             "CLAUDE_PROJECT_DIR": str(project_dir),
         }):
        auto_memory_runner.run(data_dir=tmp_data_dir)

    records = auto_memory_broker.read_queue("myproject", tmp_data_dir)
    assert len(records) == 1


def test_no_project_dir_graceful(tmp_data_dir):
    """slug 未指定 & CLAUDE_PROJECT_DIR なし → graceful exit、どのキューにも書かない。"""
    _write_corrections(tmp_data_dir, count=3)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}, clear=False):
        import os as _os
        _os.environ.pop("CLAUDE_PROJECT_DIR", None)
        auto_memory_runner.run(data_dir=tmp_data_dir)

    # キューディレクトリ自体が作られない（enqueue されない）
    queue_dir = tmp_data_dir / auto_memory_broker.QUEUE_SUBDIR
    assert not queue_dir.exists() or list(queue_dir.glob("*.jsonl")) == []


# ─── Test 7: read_recent_corrections ────────────────────────────────────────


def test_read_recent_corrections_returns_last_5(tmp_data_dir):
    """read_recent_corrections は最新 5 件を返す。"""
    _write_corrections(tmp_data_dir, count=10)

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir):
        corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir)

    assert len(corrections) == 5
    assert corrections[-1]["session_id"] == "sess-0009"
    assert corrections[0]["session_id"] == "sess-0005"


def test_read_recent_corrections_fewer_than_5(tmp_data_dir):
    """corrections.jsonl が 3 件の場合は 3 件すべてを返す。"""
    _write_corrections(tmp_data_dir, count=3)

    corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir)
    assert len(corrections) == 3


# ─── Test 8: project スコープフィルタ（#206） ────────────────────────────────


def _write_correction(
    data_dir: Path, session_id: str, timestamp: str, project_path=None,
) -> None:
    """project_path 付き / なしの correction 1件を corrections.jsonl に追記する。"""
    record = {
        "session_id": session_id,
        "timestamp": timestamp,
        "original": f"original {session_id}",
        "corrected": f"corrected {session_id}",
    }
    if project_path is not None:
        record["project_path"] = project_path
    corrections_file = data_dir / "corrections.jsonl"
    with corrections_file.open("a") as f:
        f.write(json.dumps(record) + "\n")


def test_read_recent_corrections_excludes_other_project(tmp_data_dir):
    """他 PJ の project_path を持つ correction は除外される。"""
    for i in range(3):
        _write_correction(
            tmp_data_dir, f"sess-mine-{i}", f"2026-05-25T10:0{i}:00Z", project_path="myproject",
        )
    for i in range(3):
        _write_correction(
            tmp_data_dir, f"sess-other-{i}", f"2026-05-25T11:0{i}:00Z", project_path="otherproject",
        )

    corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir, slug="myproject")

    assert len(corrections) == 3
    assert all(c["project_path"] == "myproject" for c in corrections)


def test_read_recent_corrections_includes_unattributed(tmp_data_dir):
    """project_path 欠落（未帰属）の correction は寛容に含める。"""
    _write_correction(tmp_data_dir, "sess-generic", "2026-05-25T10:00:00Z")

    corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir, slug="myproject")

    assert len(corrections) == 1
    assert corrections[0]["session_id"] == "sess-generic"


def test_read_recent_corrections_no_slug_keeps_legacy_behavior(tmp_data_dir):
    """slug 未指定（既存呼び出し元）はフィルタ無し（後方互換）。"""
    _write_correction(tmp_data_dir, "sess-a", "2026-05-25T10:00:00Z", project_path="pj-a")
    _write_correction(tmp_data_dir, "sess-b", "2026-05-25T10:01:00Z", project_path="pj-b")

    corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir)

    assert len(corrections) == 2


def test_read_recent_corrections_filters_before_slicing_tail(tmp_data_dir):
    """フィルタ後に直近 N 件を取る: 他 PJ の割り込みで自 PJ 分が押し出されない。"""
    for i in range(6):
        _write_correction(
            tmp_data_dir, f"sess-mine-{i}", f"2026-05-25T10:0{i}:00Z", project_path="myproject",
        )
    for i in range(3):
        _write_correction(
            tmp_data_dir, f"sess-other-{i}", f"2026-05-25T11:0{i}:00Z", project_path="otherproject",
        )

    corrections = auto_memory_runner.read_recent_corrections(data_dir=tmp_data_dir, slug="myproject")

    assert len(corrections) == 5
    assert all(c["project_path"] == "myproject" for c in corrections)
    assert corrections[0]["session_id"] == "sess-mine-1"
    assert corrections[-1]["session_id"] == "sess-mine-5"


def test_load_all_corrections_excludes_other_project(tmp_data_dir):
    """_load_all_corrections（ゲーティング窓）も同じフィルタを適用する。"""
    for i in range(2):
        _write_correction(
            tmp_data_dir, f"sess-mine-{i}", f"2026-05-25T10:0{i}:00Z", project_path="myproject",
        )
    _write_correction(tmp_data_dir, "sess-other", "2026-05-25T10:05:00Z", project_path="otherproject")

    all_corrections = auto_memory_runner._load_all_corrections(data_dir=tmp_data_dir, slug="myproject")

    assert len(all_corrections) == 2
    assert all(c["project_path"] == "myproject" for c in all_corrections)


def test_run_excludes_other_project_from_enqueue(tmp_data_dir, tmp_memory_dir):
    """E2E: 他 PJ の project_path を持つ correction は enqueue に混入しない（#206 本体）。"""
    _write_correction(tmp_data_dir, "sess-mine-0", "2026-05-25T10:00:00Z", project_path="myproject")
    _write_correction(tmp_data_dir, "sess-other-0", "2026-05-25T10:01:00Z", project_path="otherproject")

    with mock.patch("auto_memory_runner.DATA_DIR", tmp_data_dir), \
         mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        auto_memory_runner.run(data_dir=tmp_data_dir, slug="myproject", memory_dir=tmp_memory_dir)

    records = auto_memory_broker.read_queue("myproject", tmp_data_dir)
    assert len(records) == 1
    corrections = records[0]["corrections"]
    assert len(corrections) == 1
    assert corrections[0]["session_id"] == "sess-mine-0"
