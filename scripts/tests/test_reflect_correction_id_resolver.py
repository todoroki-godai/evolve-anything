import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFLECT = ROOT / "skills" / "reflect" / "scripts" / "reflect.py"
LIB = ROOT / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from memory_temporal import make_source_correction_id


def _load():
    spec = importlib.util.spec_from_file_location("reflect_correction_id_test", REFLECT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_resolve_source_id_uses_immutable_id_resolver_and_handles_non_dict():
    module = _load()
    correction_id = "a" * 32
    source_id = make_source_correction_id("session", "2026-09-01T00:00:00+00:00")
    records = [
        ["not", "a", "record"],
        {
            "session_id": "session",
            "timestamp": "2026-09-01T00:00:00+00:00",
            "correction_id": correction_id,
        },
    ]
    result = module.resolve_source_correction_id(records, source_id)
    assert result == {"status": "found", "correction_id": correction_id}


def test_resolve_source_id_reports_invalid_and_duplicate_ids():
    module = _load()
    source_id = make_source_correction_id("session", "time")
    invalid = [{"session_id": "session", "timestamp": "time", "correction_id": None}]
    assert module.resolve_source_correction_id(invalid, source_id)["status"] == "invalid_id"
    duplicate_id = "b" * 32
    duplicates = [
        {"session_id": "session", "timestamp": "time", "correction_id": duplicate_id},
        {"session_id": "other", "timestamp": "other", "correction_id": duplicate_id},
    ]
    assert module.resolve_source_correction_id(duplicates, source_id)["status"] == "ambiguous"


def test_resolver_cli_is_read_only_across_isolated_directory(tmp_path):
    corrections = tmp_path / "corrections.jsonl"
    correction_id = "c" * 32
    timestamp = "2026-09-01T00:00:00+00:00"
    source_id = make_source_correction_id("session", timestamp)
    corrections.write_text(
        json.dumps({
            "session_id": "session",
            "timestamp": timestamp,
            "correction_id": correction_id,
            "message": "unchanged",
        }) + "\n[1,2]\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.log").write_text("unchanged", encoding="utf-8")
    before = _snapshot(tmp_path)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)
    env["PYTHONPATH"] = str(LIB)

    completed = subprocess.run(
        [sys.executable, str(REFLECT), "--corrections-file", str(corrections), "--resolve-source-id", source_id],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"status": "found", "correction_id": correction_id}
    assert _snapshot(tmp_path) == before
