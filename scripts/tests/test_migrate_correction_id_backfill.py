import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate_correction_id_backfill.py"
VALID = "1" * 32


def _load(name="migrate_correction_id_backfill_test"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_reads_real_input_and_never_writes(tmp_path):
    module = _load("migration_dry_run")
    path = tmp_path / "corrections.jsonl"
    path.write_text('{"message":"ok"}\nnot-json\n[1,2]\n', encoding="utf-8")
    before = _sha(path)

    result = module.migrate(path)

    assert result.status == "dry_run"
    assert result.total == 3
    assert result.newly_assigned == 1
    assert result.malformed_lines == 2
    assert _sha(path) == before
    assert not list(tmp_path.glob("*.bak-*"))


def test_apply_keeps_existing_id_and_assigns_unique_valid_ids(tmp_path):
    module = _load("migration_positive")
    path = tmp_path / "corrections.jsonl"
    path.write_text(
        json.dumps({"message": "existing", "correction_id": VALID}) + "\n"
        + "\n".join(json.dumps({"message": f"new-{i}"}) for i in range(3)) + "\n",
        encoding="utf-8",
    )

    result = module.migrate(path, dry_run=False)

    records = [json.loads(line) for line in path.read_text().splitlines()]
    ids = [record["correction_id"] for record in records]
    assert result.status == "completed"
    assert records[0]["correction_id"] == VALID
    assert len(set(ids)) == 4
    assert all(module.validate_correction_id(value) for value in ids)
    assert len(list(tmp_path.glob("corrections.jsonl.bak-*"))) == 1
    assert result.initial_identity is not None
    assert result.final_identity is not None


def test_existing_to_new_collision_is_conflict_and_source_is_unchanged(tmp_path, monkeypatch):
    module = _load("migration_collision")
    path = tmp_path / "corrections.jsonl"
    path.write_text(
        json.dumps({"correction_id": VALID, "message": "existing"}) + "\n"
        + json.dumps({"message": "missing"}) + "\n",
        encoding="utf-8",
    )
    before = _sha(path)
    monkeypatch.setattr(module, "new_correction_id", lambda: VALID)
    result = module.migrate(path, dry_run=False)
    assert result.status == "conflict"
    assert result.duplicates == [VALID]
    assert _sha(path) == before


def test_normal_apply_calls_replace_and_preserves_raw_lines(tmp_path, monkeypatch):
    module = _load("migration_replace")
    path = tmp_path / "corrections.jsonl"
    unchanged = '{"correction_id": "' + VALID + '", "z": 1, "a": 2}'
    changed_dict = {"z": 3, "a": 4}
    changed = json.dumps(changed_dict, ensure_ascii=False)
    malformed = "not-json"
    path.write_text("\n".join([unchanged, changed, malformed]) + "\n", encoding="utf-8")
    generated = "2" * 32
    monkeypatch.setattr(module, "new_correction_id", lambda: generated)
    real_replace = module.os.replace
    calls = []

    def spy_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", spy_replace)
    result = module.migrate(path, dry_run=False)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert result.status == "completed"
    assert len(calls) == 1
    assert lines[0] == unchanged
    assert lines[1] == json.dumps({**changed_dict, "correction_id": generated}, ensure_ascii=False)
    assert lines[2] == malformed


def test_partial_tempfile_write_failure_leaves_source_unchanged(tmp_path, monkeypatch):
    module = _load("migration_partial_write")
    path = tmp_path / "corrections.jsonl"
    path.write_text('{"message":"original"}\n', encoding="utf-8")
    before = _sha(path)
    real_fdopen = module.os.fdopen

    class FailingWriter:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def write(self, value):
            self.wrapped.write(value[:5])
            self.wrapped.flush()
            raise OSError("failpoint after write before replace")

    monkeypatch.setattr(module.os, "fdopen", lambda *a, **k: FailingWriter(real_fdopen(*a, **k)))
    result = module.migrate(path, dry_run=False)
    assert result.status == "retry_required"
    assert "failpoint" in result.reason
    assert _sha(path) == before


def test_hash_detects_content_change_when_stat_is_forged_equal(tmp_path, monkeypatch):
    module = _load("migration_hash_conflict")
    path = tmp_path / "corrections.jsonl"
    original = '{"message":"AAAA"}\n'
    replacement = '{"message":"BBBB"}\n'
    assert len(original.encode()) == len(replacement.encode())
    path.write_text(original, encoding="utf-8")
    initial_stat = path.stat()
    real_read_text = Path.read_text
    reads = 0

    def mutate_before_second_read(self, *args, **kwargs):
        nonlocal reads
        if self == path:
            reads += 1
            if reads == 2:
                path.write_text(replacement, encoding="utf-8")
                os.utime(path, ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mutate_before_second_read)
    result = module.migrate(path, dry_run=False)
    assert result.status == "conflict"
    assert "identity/hash mismatch" in result.reason
    assert path.read_text(encoding="utf-8") == replacement


def test_post_replace_mismatch_is_incomplete(tmp_path, monkeypatch):
    module = _load("migration_incomplete")
    path = tmp_path / "corrections.jsonl"
    path.write_text('{"message":"x"}\n', encoding="utf-8")
    real_replace = module.os.replace

    def corrupt_after_replace(src, dst):
        real_replace(src, dst)
        Path(dst).write_text("corrupt\n", encoding="utf-8")

    monkeypatch.setattr(module.os, "replace", corrupt_after_replace)
    assert module.migrate(path, dry_run=False).status == "incomplete"


def test_fcntl_unavailable_rejects_apply_and_dry_run(tmp_path, monkeypatch):
    module = _load("migration_no_fcntl")
    path = tmp_path / "corrections.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(module.persistence, "_HAVE_FCNTL", False)
    assert module.migrate(path).status == "retry_required"
    assert module.migrate(path, dry_run=False).status == "retry_required"


@pytest.mark.parametrize(
    ("args", "expected"),
    [([], 0), (["--apply"], 0)],
)
def test_cli_success_exit_codes(tmp_path, args, expected):
    path = tmp_path / "corrections.jsonl"
    path.write_text('{"message":"x"}\n', encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args, str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == expected, completed.stderr


def test_cli_has_no_backup_bypass_flag():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], text=True, capture_output=True, check=False
    )
    assert "--skip-backup" not in completed.stdout


@pytest.mark.parametrize(
    ("status", "expected"),
    [("dry_run", 0), ("completed", 0), ("incomplete", 1), ("conflict", 2), ("retry_required", 3)],
)
def test_all_statuses_have_distinct_cli_exit_codes(tmp_path, monkeypatch, status, expected):
    module = _load(f"migration_exit_{status}")
    monkeypatch.setattr(module, "migrate", lambda *a, **k: module.MigrationResult(status=status))
    assert module.main([str(tmp_path / "corrections.jsonl")]) == expected
