"""implement スキル テレメトリ記録のテスト."""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestRecordUsage:
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


class TestRecordGrowthJournal:
    """growth-journal.jsonl は実 store_registry で status=dead（PR #389・#379 Step 3
    レビューで writer ゲート追加）。本クラスは writer の書込ロジック自体を検証するため
    is_dead_store を False に固定する（dead ゲートの検証は
    TestRecordGrowthJournalDeadGate に分離）。
    """

    @pytest.fixture(autouse=True)
    def _force_not_dead(self, monkeypatch):
        import store_registry
        monkeypatch.setattr(store_registry, "is_dead_store", lambda name: False)

    def test_writes_growth_journal(self, data_dir):
        from telemetry import record_growth_journal

        rec = record_growth_journal(tasks_completed=3, conformance_rate=1.0, mode="standard")
        assert rec["type"] == "implementation"
        assert rec["source"] == "implement-skill"
        assert rec["tasks_completed"] == 3
        assert rec["phase"] == "unknown"

        records = _load_jsonl(data_dir / "growth-journal.jsonl")
        assert len(records) == 1
        assert records[0]["type"] == "implementation"

    def test_conformance_rate_in_journal(self, data_dir):
        from telemetry import record_growth_journal

        rec = record_growth_journal(tasks_completed=2, conformance_rate=0.6667, mode="parallel")
        assert rec["conformance_rate"] == 0.67


class TestRecordGrowthJournalDeadGate:
    """growth-journal.jsonl の status=dead ゲート（#379 Step 3 レビュー対応）。

    quality_engine.record_quality_score / growth_journal.emit_crystallization のゲート
    追加時に静的契約テストで発見された第3の未ゲート writer（SKILL.md の完了時記録手順から
    呼ばれる）。
    """

    def test_dead_store_skips_write_but_returns_record(self, data_dir):
        """実 registry で status=dead のため write されないが、戻り値契約（dict）は維持する。"""
        from telemetry import record_growth_journal

        rec = record_growth_journal(tasks_completed=3, conformance_rate=1.0, mode="standard")
        assert isinstance(rec, dict)
        assert rec["tasks_completed"] == 3
        assert not (data_dir / "growth-journal.jsonl").exists()

    def test_registry_import_failure_fails_open(self, data_dir, monkeypatch):
        """store_registry が import 不能な環境ではゲート起因で書込を止めない（fail-open）。"""
        import sys
        from telemetry import record_growth_journal

        monkeypatch.setitem(sys.modules, "store_registry", None)
        record_growth_journal(tasks_completed=1, conformance_rate=1.0, mode="standard")

        records = _load_jsonl(data_dir / "growth-journal.jsonl")
        assert len(records) == 1
