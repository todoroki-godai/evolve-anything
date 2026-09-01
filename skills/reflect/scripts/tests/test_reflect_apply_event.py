"""#587 reflect CLI の2フェーズ反映イベント統合テスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest


_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "skills" / "reflect" / "scripts"))

import pillar2_metrics
import reflect
import rl_common
from rl_common.correction_id import AppendResult


def _correction(correction_id: str, session_id: str) -> dict:
    return {
        "correction_id": correction_id,
        "session_id": session_id,
        "timestamp": "2026-09-01T00:00:00+00:00",
        "extracted_learning": "Use the stable API",
        "reflect_status": "promoted",
        "project_path": None,
    }


def _write_corrections(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _run_apply(corrections: Path, target: str, draft_file: Path, session_id: str) -> dict:
    source_id = reflect.make_source_correction_id(
        session_id, "2026-09-01T00:00:00+00:00"
    )
    with mock.patch("sys.argv", [
        "reflect.py", "--apply", source_id,
        "--target-path", target,
        "--draft-line-file", str(draft_file),
        "--corrections-file", str(corrections),
    ]):
        reflect.main()
    return source_id


def _read_events() -> list[dict]:
    path = rl_common.DATA_DIR / "reflect_apply_events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_apply_writes_classified_kind(tmp_path, capsys):
    corrections = tmp_path / "corrections.jsonl"
    _write_corrections(corrections, [_correction("a" * 32, "session-a")])
    target = Path.home() / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Use the stable API\n", encoding="utf-8")
    draft = tmp_path / "draft.txt"
    draft.write_text("Use the stable API", encoding="utf-8")

    _run_apply(corrections, str(target), draft, "session-a")
    output = json.loads(capsys.readouterr().out)
    events = _read_events()

    assert output["status"] == "applied"
    assert [event["event_type"] for event in events] == [
        "correction_apply_attempted", "correction_applied"
    ]
    assert events[0]["reflect_target_kind"] == "global_claude_md"
    result = pillar2_metrics.count_applied_reflections(
        tmp_path,
        corrections_path=corrections,
        events_path=rl_common.DATA_DIR / "reflect_apply_events.jsonl",
    )
    assert result["count"] == 1
    assert result["measured"] is True
    assert result["applied_list"][0]["reconciled"] is False


def test_apply_aborts_when_phase1_append_fails(tmp_path, capsys):
    corrections = tmp_path / "corrections.jsonl"
    record = _correction("a" * 32, "session-a")
    _write_corrections(corrections, [record])
    target = tmp_path / "target.md"
    target.write_text("Use the stable API\n", encoding="utf-8")
    draft = tmp_path / "draft.txt"
    draft.write_text("Use the stable API", encoding="utf-8")

    with mock.patch.object(
        reflect,
        "append_unique_record",
        return_value=AppendResult("retry_required", "locked"),
    ), mock.patch.object(reflect, "update_reflect_status") as update:
        with pytest.raises(SystemExit):
            _run_apply(corrections, str(target), draft, "session-a")

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "pillar2_event_failed"
    update.assert_not_called()
    assert reflect.load_corrections(corrections)[0]["reflect_status"] == "promoted"


def test_apply_normalizes_path_before_grouping(tmp_path, capsys, monkeypatch):
    corrections = tmp_path / "corrections.jsonl"
    _write_corrections(corrections, [
        _correction("a" * 32, "session-a"),
        _correction("b" * 32, "session-b"),
    ])
    target = Path.home() / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Use the stable API\n", encoding="utf-8")
    draft = tmp_path / "draft.txt"
    draft.write_text("Use the stable API", encoding="utf-8")
    monkeypatch.chdir(target.parent)

    _run_apply(corrections, "CLAUDE.md", draft, "session-a")
    capsys.readouterr()
    _run_apply(corrections, str(target), draft, "session-b")
    capsys.readouterr()
    events = _read_events()
    attempts = [event for event in events if event["event_type"] == "correction_apply_attempted"]

    assert attempts[0]["reflect_target_path"] == attempts[1]["reflect_target_path"]
    result = pillar2_metrics.count_applied_reflections(
        tmp_path,
        corrections_path=corrections,
        events_path=rl_common.DATA_DIR / "reflect_apply_events.jsonl",
    )
    assert result["count"] == 1


def test_skip_writes_audit_event(tmp_path, capsys):
    corrections = tmp_path / "corrections.jsonl"
    _write_corrections(corrections, [_correction("a" * 32, "session-a")])
    source_id = reflect.make_source_correction_id(
        "session-a", "2026-09-01T00:00:00+00:00"
    )
    with mock.patch("sys.argv", [
        "reflect.py", "--skip", source_id,
        "--corrections-file", str(corrections),
    ]):
        reflect.main()

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "skipped"
    assert _read_events()[0]["event_type"] == "correction_skipped"
