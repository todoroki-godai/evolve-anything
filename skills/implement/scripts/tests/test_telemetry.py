"""implement スキル テレメトリ記録のテスト."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    import rl_common

    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    return tmp_path


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestRecordUsage:
    def test_routes_usage_through_store_write(self, data_dir, monkeypatch):
        import telemetry

        writes = []
        monkeypatch.setattr(
            telemetry,
            "store_write",
            lambda store_name, record: writes.append((store_name, record)),
            raising=False,
        )

        record = telemetry.record_usage(
            project="test-project",
            tasks_total=1,
            tasks_completed=1,
            mode="standard",
            conformance_rate=1.0,
        )

        assert writes == [("usage.jsonl", record)]

    def test_writes_usage_record(self, data_dir):
        from telemetry import record_usage

        rec = record_usage(
            project="test-project",
            tasks_total=3,
            tasks_completed=3,
            mode="standard",
            conformance_rate=1.0,
        )
        assert rec["skill"] == "implement"
        assert rec["project"] == "test-project"
        assert rec["tasks_total"] == 3
        assert rec["tasks_completed"] == 3
        assert rec["mode"] == "standard"
        assert rec["conformance_rate"] == 1.0
        assert rec["outcome"] == "success"
        assert "ts" in rec

        records = _load_jsonl(data_dir / "usage.jsonl")
        assert len(records) == 1
        assert records[0]["skill"] == "implement"

    def test_appends_multiple_records(self, data_dir):
        from telemetry import record_usage

        record_usage(project="p1", tasks_total=2, tasks_completed=2, mode="standard", conformance_rate=1.0)
        record_usage(project="p2", tasks_total=5, tasks_completed=4, mode="parallel", conformance_rate=0.8, lanes=3)

        records = _load_jsonl(data_dir / "usage.jsonl")
        assert len(records) == 2
        assert records[1]["mode"] == "parallel"
        assert records[1]["lanes"] == 3

    def test_conformance_rate_rounded(self, data_dir):
        from telemetry import record_usage

        rec = record_usage(project="p", tasks_total=3, tasks_completed=2, mode="standard", conformance_rate=0.6667)
        assert rec["conformance_rate"] == 0.67

    def test_partial_outcome(self, data_dir):
        from telemetry import record_usage

        rec = record_usage(
            project="p", tasks_total=5, tasks_completed=3, mode="parallel", conformance_rate=0.6, outcome="partial"
        )
        assert rec["outcome"] == "partial"


def test_telemetry_reference_uses_write_barrier():
    reference = Path(__file__).resolve().parents[2] / "references" / "telemetry.md"
    content = reference.read_text()

    assert 'store_write("usage.jsonl", record)' in content
    assert 'open(os.path.join(data_dir, "usage.jsonl")' not in content


def test_telemetry_module_loads_in_isolated_python():
    module_path = Path(__file__).resolve().parents[1] / "telemetry.py"
    code = (
        "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('implement_telemetry', {str(module_path)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "assert callable(m.record_usage)"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True)
