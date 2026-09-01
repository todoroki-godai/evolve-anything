import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from rl_common import AppendResult, new_correction_id, resolve_correction_id, validate_correction_id


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_w3_real_backfill_writer_persists_ids_and_reports_counts(tmp_path, monkeypatch):
    module = _load("backfill_ids", ROOT / "scripts" / "backfill_preceding_tool_calls.py")
    monkeypatch.setattr(module, "_DATA_DIR", tmp_path)
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "s1.jsonl").write_text(json.dumps({
        "timestamp": "2026-09-01T00:00:00+00:00",
        "message": {"role": "user", "content": "いや、そうじゃなくて別の方法にして"},
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(module, "_PROJECTS_DIR", projects)
    records = module.process_sessions(days=1, max_files=1)

    result = module.persist_to_corrections(records)

    saved = [json.loads(line) for line in (tmp_path / "corrections.jsonl").read_text().splitlines()]
    assert result == {"appended": 1, "failed_index": None, "failure_status": None, "reason": None, "unprocessed": 0}
    assert all(validate_correction_id(record["correction_id"]) for record in saved)
    assert len({record["correction_id"] for record in saved}) == 1
    assert all(resolve_correction_id(saved, record["correction_id"]).status == "found" for record in saved)


def test_w3_stops_on_first_append_failure(tmp_path, monkeypatch):
    module = _load("backfill_failfast", ROOT / "scripts" / "backfill_preceding_tool_calls.py")
    monkeypatch.setattr(module, "_DATA_DIR", tmp_path)
    calls = []

    def fail_second(path, record):
        calls.append(record)
        return AppendResult("appended" if len(calls) == 1 else "duplicate_id", "collision")

    monkeypatch.setattr(module, "append_correction_record", fail_second)
    result = module.persist_to_corrections([
        {"correction_id": new_correction_id(), "session_id": "s1", "timestamp": "t1"},
        {"correction_id": new_correction_id(), "session_id": "s2", "timestamp": "t2"},
        {"correction_id": new_correction_id(), "session_id": "s3", "timestamp": "t3"},
    ])
    assert len(calls) == 2
    assert result["appended"] == 1
    assert result["failed_index"] == 1
    assert result["failure_status"] == "duplicate_id"
    assert result["unprocessed"] == 1


def test_w4_real_queue_migration_only_clears_after_all_appends(tmp_path, monkeypatch):
    module = _load("reflect_queue_ids", ROOT / "scripts" / "migrate_reflect_queue.py")
    queue = tmp_path / "learnings-queue.json"
    corrections = tmp_path / "corrections.jsonl"
    queue.write_text(json.dumps([
        {"timestamp": "t1", "message": "one"},
        {"timestamp": "t2", "message": "two"},
    ]), encoding="utf-8")
    monkeypatch.setattr(module, "LEARNINGS_QUEUE", queue)
    monkeypatch.setattr(module, "CORRECTIONS_FILE", corrections)

    result = module.migrate()

    saved = [json.loads(line) for line in corrections.read_text().splitlines()]
    assert result["status"] == "completed"
    assert result["appended"] == 2
    assert queue.read_text(encoding="utf-8") == "[]"
    assert all(validate_correction_id(record["correction_id"]) for record in saved)


def test_w4_partial_failure_preserves_queue(tmp_path, monkeypatch):
    module = _load("reflect_queue_failfast", ROOT / "scripts" / "migrate_reflect_queue.py")
    queue = tmp_path / "learnings-queue.json"
    original = json.dumps([{"message": "one"}, {"message": "two"}, {"message": "three"}])
    queue.write_text(original, encoding="utf-8")
    monkeypatch.setattr(module, "LEARNINGS_QUEUE", queue)
    monkeypatch.setattr(module, "CORRECTIONS_FILE", tmp_path / "corrections.jsonl")
    calls = []

    def fail_second(path, record):
        calls.append(record)
        return AppendResult("appended" if len(calls) == 1 else "invalid_id", "bad")

    monkeypatch.setattr(module, "append_correction_record", fail_second)
    result = module.migrate()
    assert result["status"] == "partial"
    assert result["appended"] == 1
    assert result["failed_index"] == 1
    assert result["unprocessed"] == 1
    assert queue.read_text(encoding="utf-8") == original


def test_w1_to_w4_do_not_call_generic_store_writers_directly():
    """correction 保存入口を専用境界1本へ固定する構造検査。"""
    paths = [
        ROOT / "hooks" / "correction_detect.py",
        ROOT / "scripts" / "lib" / "correction_semantic" / "promote.py",
        ROOT / "scripts" / "backfill_preceding_tool_calls.py",
        ROOT / "scripts" / "migrate_reflect_queue.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "store_write(" not in source, path
        assert "store_write_raw(" not in source, path
        assert "append_correction_record(" in source, path
