"""save_state.py の corrections snapshot project スコープフィルタのテスト（#206）。

corrections.jsonl は全 PJ 共有ストアのため、PreCompact checkpoint の corrections_snapshot に
他 PJ の correction が混入していた。restore_state.py が SessionStart/PostCompact でこの
snapshot を Claude context へ直接注入するため、auto_memory_runner の enqueue 経路より
露出範囲が広い。すべて LLM-free。
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
_LIB = _HOOKS.parent / "scripts" / "lib"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_LIB))

import save_state
import common


@pytest.fixture
def tmp_data_dir(tmp_path):
    d = tmp_path / "evolve-anything"
    d.mkdir()
    return d


def _write_correction(data_dir: Path, session_id: str, timestamp: str, project_path=None) -> None:
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


# ─── _load_corrections_snapshot ─────────────────────────────────────────────


def test_missing_file_returns_empty(tmp_data_dir):
    assert save_state._load_corrections_snapshot(data_dir=tmp_data_dir) == []


def test_no_slug_keeps_legacy_behavior(tmp_data_dir):
    """slug 未指定（既存呼び出し元）はフィルタ無し（後方互換）。"""
    _write_correction(tmp_data_dir, "sess-a", "2026-05-25T10:00:00Z", project_path="pj-a")
    _write_correction(tmp_data_dir, "sess-b", "2026-05-25T10:01:00Z", project_path="pj-b")

    records = save_state._load_corrections_snapshot(data_dir=tmp_data_dir)
    assert len(records) == 2


def test_excludes_other_project(tmp_data_dir):
    """他 PJ の project_path を持つ correction は除外される。"""
    _write_correction(tmp_data_dir, "sess-mine", "2026-05-25T10:00:00Z", project_path="myproject")
    _write_correction(tmp_data_dir, "sess-other", "2026-05-25T10:01:00Z", project_path="otherproject")

    records = save_state._load_corrections_snapshot(data_dir=tmp_data_dir, slug="myproject")

    assert len(records) == 1
    assert records[0]["session_id"] == "sess-mine"


def test_includes_unattributed(tmp_data_dir):
    """project_path 欠落（未帰属）の correction は寛容に含める。"""
    _write_correction(tmp_data_dir, "sess-generic", "2026-05-25T10:00:00Z")

    records = save_state._load_corrections_snapshot(data_dir=tmp_data_dir, slug="myproject")

    assert len(records) == 1


# ─── handle_pre_compact E2E ─────────────────────────────────────────────────


def test_handle_pre_compact_snapshot_scoped_to_project_dir(tmp_data_dir, tmp_path):
    """E2E: CLAUDE_PROJECT_DIR から解決した slug で snapshot がスコープされる（#206 本体）。"""
    _write_correction(tmp_data_dir, "sess-mine", "2026-05-25T10:00:00Z", project_path="myproject")
    _write_correction(tmp_data_dir, "sess-other", "2026-05-25T10:01:00Z", project_path="otherproject")

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    checkpoints_dir = tmp_path / "checkpoints"

    with mock.patch.object(common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(common, "CHECKPOINTS_DIR", checkpoints_dir), \
         mock.patch.object(common, "ensure_data_dir", return_value=None), \
         mock.patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}), \
         mock.patch.object(save_state, "_collect_work_context", return_value={}), \
         mock.patch.object(common, "cleanup_old_checkpoints", return_value=None):
        save_state.handle_pre_compact({"session_id": "s1"})

    checkpoint_file = checkpoints_dir / "s1.json"
    assert checkpoint_file.exists()
    checkpoint = json.loads(checkpoint_file.read_text())
    snapshot = checkpoint["corrections_snapshot"]
    assert len(snapshot) == 1
    assert snapshot[0]["session_id"] == "sess-mine"


def test_handle_pre_compact_no_project_dir_keeps_unfiltered(tmp_data_dir, tmp_path):
    """CLAUDE_PROJECT_DIR 未設定時は slug 解決不能につきフィルタしない（従来挙動維持）。"""
    _write_correction(tmp_data_dir, "sess-a", "2026-05-25T10:00:00Z", project_path="pj-a")
    _write_correction(tmp_data_dir, "sess-b", "2026-05-25T10:01:00Z", project_path="pj-b")

    checkpoints_dir = tmp_path / "checkpoints"

    with mock.patch.object(common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(common, "CHECKPOINTS_DIR", checkpoints_dir), \
         mock.patch.object(common, "ensure_data_dir", return_value=None), \
         mock.patch.dict("os.environ", {}, clear=False), \
         mock.patch.object(save_state, "_collect_work_context", return_value={}), \
         mock.patch.object(common, "cleanup_old_checkpoints", return_value=None):
        import os as _os
        _os.environ.pop("CLAUDE_PROJECT_DIR", None)
        save_state.handle_pre_compact({"session_id": "s2"})

    checkpoint_file = checkpoints_dir / "s2.json"
    checkpoint = json.loads(checkpoint_file.read_text())
    assert len(checkpoint["corrections_snapshot"]) == 2
