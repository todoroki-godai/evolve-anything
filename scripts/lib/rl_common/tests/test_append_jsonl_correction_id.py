import json
import multiprocessing
import sys
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parents[2]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from rl_common import persistence
from rl_common.correction_id import append_correction_record


VALID_ID = "a" * 32


@pytest.mark.parametrize(
    "value",
    [pytest.param(None, id="missing-or-none"), "", 12345, [1, 2], {"x": 1}, True],
)
def test_append_rejects_invalid_id_without_writing(tmp_path, value):
    path = tmp_path / "corrections.jsonl"
    record = {"message": "keep me"}
    if value is not None:
        record["correction_id"] = value

    result = append_correction_record(path, record)

    assert result.status == "invalid_id"
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("value", ["a" * 31, "a" * 33, "A" + "a" * 31])
def test_append_rejects_invalid_format(tmp_path, value):
    path = tmp_path / "corrections.jsonl"
    result = append_correction_record(path, {"correction_id": value})
    assert result.status == "invalid_id"
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_append_preserves_the_complete_record(tmp_path):
    path = tmp_path / "corrections.jsonl"
    record = {
        "correction_id": VALID_ID,
        "message": "値を変えない",
        "session_id": "session-1",
        "reflect_status": "pending",
    }

    result = append_correction_record(path, record)

    assert result.status == "appended"
    assert json.loads(path.read_text(encoding="utf-8")) == record


def test_same_id_is_rejected_but_distinct_ids_are_preserved(tmp_path):
    path = tmp_path / "corrections.jsonl"
    first = {"correction_id": "1" * 32, "message": "first"}
    second = {"correction_id": "2" * 32, "message": "second"}

    assert append_correction_record(path, first).status == "appended"
    assert append_correction_record(path, first).status == "duplicate_id"
    assert append_correction_record(path, second).status == "appended"
    assert [json.loads(line) for line in path.read_text().splitlines()] == [first, second]


def test_fcntl_unavailable_is_rejected(monkeypatch, tmp_path):
    from rl_common import correction_id

    monkeypatch.setattr(correction_id.persistence, "_HAVE_FCNTL", False)
    result = append_correction_record(
        tmp_path / "corrections.jsonl", {"correction_id": VALID_ID}
    )
    assert result.status == "unsupported_platform"


def _concurrent_append(path, record, queue):
    result = append_correction_record(Path(path), record)
    queue.put(result.status)


def _append_pausing_after_unlock(path, record, unlocked, may_close, queue):
    """P1を unlock 後・with 終了前（暗黙close前）で決定論的に停止する。"""
    real_flock = persistence._fcntl.flock

    def controlled_flock(file_obj, operation):
        result = real_flock(file_obj, operation)
        if operation == persistence._fcntl.LOCK_UN:
            unlocked.set()
            assert may_close.wait(5)
        return result

    persistence._fcntl.flock = controlled_flock
    queue.put(append_correction_record(Path(path), record).status)


@pytest.mark.skipif(not persistence._HAVE_FCNTL, reason="fcntl required")
def test_two_processes_cannot_append_the_same_id(tmp_path):
    """unlock後・close前へ同期し、同一ID追記の順序を決定論的に固定する。"""
    path = tmp_path / "corrections.jsonl"
    record = {"correction_id": VALID_ID, "message": "same"}
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    unlocked = ctx.Event()
    may_close = ctx.Event()
    first = ctx.Process(
        target=_append_pausing_after_unlock,
        args=(path, record, unlocked, may_close, queue),
    )
    first.start()
    assert unlocked.wait(5), "P1 did not reach the post-unlock/pre-close synchronization point"

    # P1は既にunlock済みだがclose前。flushがunlock前ならP2は必ずduplicateを見る。
    second = ctx.Process(target=_concurrent_append, args=(path, record, queue))
    second.start()
    second.join(5)
    may_close.set()
    first.join(5)
    for process in (first, second):
        process.join(5)
        assert process.exitcode == 0

    assert sorted(queue.get(timeout=1) for _ in range(2)) == ["appended", "duplicate_id"]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_complete_record_survives_queue_drain_prompt_path(tmp_path):
    """保存済みrecord全体が enqueue→read→emit→prompt を欠落なく通る。"""
    from auto_memory_broker import _build_prompt, emit_memory_requests, enqueue, read_queue

    path = tmp_path / "corrections.jsonl"
    record = {
        "correction_id": VALID_ID,
        "message": "message-value",
        "session_id": "session-value",
        "timestamp": "timestamp-value",
        "reflect_status": "pending-value",
        "correction_type": "type-value",
    }
    assert append_correction_record(path, record).status == "appended"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert enqueue([persisted], "project", tmp_path)
    queued = read_queue("project", tmp_path)
    emitted = emit_memory_requests(queued)
    prompt = emitted["requests"][0]["prompt"]
    assert prompt == _build_prompt([record])
    for value in record.values():
        assert value in prompt


@pytest.mark.parametrize("field", ["message", "reflect_status", "correction_type"])
def test_prompt_reflects_each_semantic_field_independently(field):
    from auto_memory_broker import _build_prompt

    base = {
        "correction_id": VALID_ID,
        "message": "message-a",
        "reflect_status": "status-a",
        "correction_type": "type-a",
    }
    changed = dict(base)
    changed[field] += "-changed"
    assert _build_prompt([changed]) != _build_prompt([base])
    assert changed[field] in _build_prompt([changed])
