import json
import multiprocessing
import os
import sys
from collections import Counter
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parents[2]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from rl_common import persistence
import rl_common
from rl_common.correction_id import append_correction_record, append_unique_record


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


def test_duplicate_check_reads_unicode_separators_as_record_content(tmp_path):
    path = tmp_path / "corrections.jsonl"
    record = {"correction_id": VALID_ID, "message": "LS:\u2028 PS:\u2029 NEL:\u0085"}

    assert append_correction_record(path, record).status == "appended"
    before = path.read_bytes()

    assert append_correction_record(path, record).status == "duplicate_id"
    assert path.read_bytes() == before


def test_append_unique_record_resolves_registered_store_from_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    record = {"correction_id": VALID_ID, "event_type": "correction_skipped"}

    result = append_unique_record("reflect_apply_events.jsonl", record)

    assert result.status == "appended"
    assert json.loads((tmp_path / "reflect_apply_events.jsonl").read_text()) == record


def test_append_unique_record_rejects_duplicate_id(tmp_path, monkeypatch):
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    record = {"correction_id": VALID_ID}
    assert append_unique_record("reflect_apply_events.jsonl", record).status == "appended"
    assert append_unique_record("reflect_apply_events.jsonl", record).status == "duplicate_id"


def test_append_unique_record_rejects_unregistered_store(tmp_path, monkeypatch):
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    result = append_unique_record("not-registered.jsonl", {"correction_id": VALID_ID})
    assert result.status == "unregistered_store"
    assert not (tmp_path / "not-registered.jsonl").exists()


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
        # file_lock は int fd、append_jsonl は file object を渡す。新設した外側
        # sidecar lock ではなく、データファイル自身の unlock だけを同期点にする。
        if operation == persistence._fcntl.LOCK_UN and hasattr(file_obj, "fileno"):
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
    second.join(0.2)
    assert second.is_alive(), "P2 must wait for the outer corrections sidecar lock"
    may_close.set()
    first.join(5)
    second.join(5)
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


# --- 判定がロックの内側で行われることの決定論的固定（#593 実装レビュー [Must]）---
#
# 「2プロセスが同じ ID を同時に追記できない」ことを見る並行試験は、同期点より**前**の
# 順序を固定できない。重複判定を flock 取得の前へ移す変異（TOCTOU の再導入）を入れても、
# 同期点が「P1 の unlock 後に P2 を開始する」である限り P2 は P1 の行を読めてしまい、
# 試験は緑のまま通る。実際にはその変異下で、P1 が「判定通過後・ロック待ち」で止まると
# 同一 ID が2行とも appended になる。
#
# よって順序そのものを記録して assert する。実装変更ではなくテストで固定する。


def test_exclusive_lock_is_acquired_before_duplicate_check(tmp_path, monkeypatch):
    """LOCK_EX の取得は duplicate_check の評価より前でなければならない。"""
    if not persistence._HAVE_FCNTL:
        pytest.skip("fcntl unavailable")

    calls: list[str] = []
    real_flock = persistence._fcntl.flock

    def spy_flock(fd, operation):
        if operation == persistence._fcntl.LOCK_EX:
            calls.append("lock_ex")
        elif operation == persistence._fcntl.LOCK_UN:
            calls.append("lock_un")
        return real_flock(fd, operation)

    monkeypatch.setattr(persistence._fcntl, "flock", spy_flock)

    def probe(existing):
        calls.append("duplicate_check")
        return False

    path = tmp_path / "corrections.jsonl"
    result = persistence.append_jsonl(
        path, {"correction_id": VALID_ID}, duplicate_check=probe
    )

    assert result.status == "written"
    assert "lock_ex" in calls, f"LOCK_EX が取得されていない: {calls}"
    assert "duplicate_check" in calls, f"duplicate_check が評価されていない: {calls}"
    assert calls.index("lock_ex") < calls.index("duplicate_check"), (
        f"重複判定がロックの外側で行われている（TOCTOU）: {calls}"
    )
    assert calls.index("duplicate_check") < calls.index("lock_un"), (
        f"重複判定がロック解放後に行われている: {calls}"
    )


def test_write_happens_inside_the_same_lock_as_the_duplicate_check(tmp_path, monkeypatch):
    """書込みも同じロック区間の内側で行われる（判定と書込みの不可分性）。"""
    if not persistence._HAVE_FCNTL:
        pytest.skip("fcntl unavailable")

    calls: list[str] = []
    real_flock = persistence._fcntl.flock

    def spy_flock(fd, operation):
        if operation == persistence._fcntl.LOCK_EX:
            calls.append("lock_ex")
        elif operation == persistence._fcntl.LOCK_UN:
            calls.append("lock_un")
        return real_flock(fd, operation)

    monkeypatch.setattr(persistence._fcntl, "flock", spy_flock)

    path = tmp_path / "corrections.jsonl"

    def probe(existing):
        # 判定時点ではまだ自分の行は書かれていない。
        calls.append(f"duplicate_check:{len(existing)}")
        return False

    persistence.append_jsonl(path, {"correction_id": VALID_ID}, duplicate_check=probe)
    persistence.append_jsonl(path, {"correction_id": "b" * 32}, duplicate_check=probe)

    assert calls == [
        "lock_ex",
        "duplicate_check:0",
        "lock_un",
        "lock_ex",
        "duplicate_check:1",
        "lock_un",
    ], f"ロック区間と判定・書込みの順序が崩れている: {calls}"


def test_corrections_lock_path_resolves_symlink_alias(tmp_path):
    from rl_common.correction_id import _corrections_lock_path

    real = tmp_path / "real" / "corrections.jsonl"
    real.parent.mkdir()
    real.write_text("", encoding="utf-8")
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(real)

    assert _corrections_lock_path(real) == _corrections_lock_path(alias)


def test_snapshot_identities_detects_legacy_same_count_replacement():
    from rl_common.correction_id import (
        UnexpectedCorrectionLossError,
        assert_no_unexpected_content_loss,
        snapshot_identities,
    )

    before = snapshot_identities('{"message":"A"}\n{"message":"B"}\n')
    after = snapshot_identities('{"message":"B"}\n{"message":"B"}\n')

    with pytest.raises(UnexpectedCorrectionLossError):
        assert_no_unexpected_content_loss(before, after)


def test_snapshot_identities_preserves_duplicate_multiplicity():
    from rl_common.correction_id import snapshot_identities

    identity = next(iter(snapshot_identities('{"message":"same"}\n')))
    assert snapshot_identities('{"message":"same"}\n{"message":"same"}\n') == Counter(
        {identity: 2}
    )


def test_declared_touched_identity_allows_intended_change():
    from rl_common.correction_id import assert_no_unexpected_content_loss, snapshot_identities

    before_text = '{"correction_id":"' + VALID_ID + '","status":"old"}\n'
    after_text = '{"correction_id":"' + VALID_ID + '","status":"new"}\n'
    before = snapshot_identities(before_text)
    assert_no_unexpected_content_loss(
        before,
        snapshot_identities(after_text),
        touched_before=snapshot_identities(before_text),
    )


def test_atomic_write_preserves_existing_mode(tmp_path):
    from rl_common.correction_id import atomic_write_text_preserving_mode

    path = tmp_path / "corrections.jsonl"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o640)
    atomic_write_text_preserving_mode(path, "new")
    assert path.read_text(encoding="utf-8") == "new"
    assert os.stat(path).st_mode & 0o777 == 0o640


def test_iter_indexed_lines_has_one_record_index_contract():
    text = '\n{"name":"zero"}\nbroken\n[1]\n {"name":"two"} \n'
    lines = list(persistence.iter_indexed_lines(text))
    assert [(x.record_index, x.physical_line_index, x.record, x.raw_line) for x in lines] == [
        (0, 1, {"name": "zero"}, '{"name":"zero"}'),
        (1, 3, [1], '[1]'),
        (2, 4, {"name": "two"}, ' {"name":"two"} '),
    ]


def test_record_identity_prefers_correction_id_and_supports_legacy():
    assert persistence.record_identity({"correction_id": VALID_ID, "session_id": "ignored"}) == (
        "id",
        VALID_ID,
    )
    assert persistence.record_identity({"session_id": "s", "timestamp": "t"}) == (
        "legacy",
        "s",
        "t",
    )


def test_append_acquires_sidecar_before_file_lock(tmp_path, monkeypatch):
    from rl_common import correction_id

    calls = []

    class Lock:
        def __enter__(self):
            calls.append("sidecar_enter")

        def __exit__(self, *_args):
            calls.append("sidecar_exit")

    monkeypatch.setattr(correction_id, "corrections_write_lock", lambda _path: Lock())
    real_append = persistence.append_jsonl

    def append(*args, **kwargs):
        calls.append("append_jsonl")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(persistence, "append_jsonl", append)
    result = append_correction_record(tmp_path / "corrections.jsonl", {"correction_id": VALID_ID})
    assert result.status == "appended"
    assert calls == ["sidecar_enter", "append_jsonl", "sidecar_exit"]
