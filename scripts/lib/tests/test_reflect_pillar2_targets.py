"""#632: memory / refs の reflect --apply 配線と柱2計上を検証する。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "reflect" / "scripts"))

import pillar2_metrics  # noqa: E402
import reflect  # noqa: E402
import rl_common  # noqa: E402
from reflect_fold import fold_corrections  # noqa: E402


DRAFT_LINE = "Count this applied reflection in pillar 2"
TIMESTAMP = "2026-09-09T00:00:00+00:00"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _run_apply_and_measure(
    tmp_path: Path,
    capsys,
    *,
    target_path: Path,
    expected_kind: str,
) -> tuple[dict, object, dict]:
    corrections_path = tmp_path / "corrections.jsonl"
    draft_path = tmp_path / "draft-line.txt"
    session_id = "session-632"
    source_id = reflect.make_source_correction_id(session_id, TIMESTAMP)
    base = {
        "correction_id": "a" * 32,
        "session_id": session_id,
        "timestamp": TIMESTAMP,
        "extracted_learning": DRAFT_LINE,
        "reflect_status": "pending",
        "project_path": None,
    }
    _write_jsonl(corrections_path, [base])
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists():
        target_path.write_text(DRAFT_LINE + "\n", encoding="utf-8")
    draft_path.write_text(DRAFT_LINE + "\n", encoding="utf-8")

    with mock.patch(
        "sys.argv",
        [
            "reflect.py",
            "--apply",
            source_id,
            "--target-path",
            str(target_path),
            "--draft-line-file",
            str(draft_path),
            "--corrections-file",
            str(corrections_path),
            "--project-dir",
            str(tmp_path / "project"),
        ],
    ):
        reflect.main()
    assert json.loads(capsys.readouterr().out)["status"] == "applied"

    event_path = rl_common.DATA_DIR / "reflect_apply_events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    attempted = next(event for event in events if event["event_type"] == "correction_apply_attempted")
    assert attempted["reflect_target_kind"] == expected_kind

    bases = reflect.load_corrections(corrections_path)
    folded, fold_health = fold_corrections(bases, events)
    result = pillar2_metrics.count_applied_reflections(
        tmp_path / "project",
        corrections_path=corrections_path,
        events_path=event_path,
        now=datetime.now(timezone.utc),
    )
    return attempted, folded[0], {"fold_health": fold_health, "metrics": result}


@pytest.mark.parametrize(
    ("relative_path", "expected_kind"),
    [
        ("refs/verification.md", "global_refs"),
        ("projects/-tmp-project/memory/MEMORY.md", "project_memory"),
    ],
)
def test_reflect_apply_counts_new_pillar2_target_kinds(
    tmp_path, capsys, relative_path, expected_kind
):
    target = Path.home() / ".claude" / relative_path

    attempted, folded, observed = _run_apply_and_measure(
        tmp_path, capsys, target_path=target, expected_kind=expected_kind
    )
    result = observed["metrics"]

    assert attempted["reflect_target_kind"] == expected_kind
    assert observed["fold_health"].invalid_events == 0
    assert folded.has_pillar2_fields is True
    assert result["count"] == 1
    assert result["measured"] is True
    assert result["applied_list"][0]["target_kind"] == expected_kind


def test_reflect_apply_classifies_symlink_by_resolved_target(tmp_path, capsys):
    refs_target = Path.home() / ".claude" / "refs" / "real.md"
    refs_target.parent.mkdir(parents=True, exist_ok=True)
    refs_target.write_text(DRAFT_LINE + "\n", encoding="utf-8")
    outside_link = tmp_path / "outside-link.md"
    outside_link.symlink_to(refs_target)

    _, _, observed = _run_apply_and_measure(
        tmp_path, capsys, target_path=outside_link, expected_kind="global_refs"
    )
    assert observed["metrics"]["count"] == 1


def test_reflect_apply_excludes_refs_symlink_to_outside(tmp_path, capsys):
    outside_target = tmp_path / "outside.md"
    outside_target.write_text(DRAFT_LINE + "\n", encoding="utf-8")
    refs_link = Path.home() / ".claude" / "refs" / "outside-link.md"
    refs_link.parent.mkdir(parents=True, exist_ok=True)
    refs_link.symlink_to(outside_target)

    _, _, observed = _run_apply_and_measure(
        tmp_path, capsys, target_path=refs_link, expected_kind="other"
    )
    result = observed["metrics"]
    assert result["other_kind_count"] == 1
    assert result["count"] == 0
    assert result["measured"] is True


@pytest.mark.parametrize(
    "relative_path",
    [
        "refs-old/verification.md",
        "projects/-tmp-project/CLAUDE.md",
    ],
)
def test_reflect_apply_excludes_similar_non_roots(tmp_path, capsys, relative_path):
    target = Path.home() / ".claude" / relative_path

    _, _, observed = _run_apply_and_measure(
        tmp_path, capsys, target_path=target, expected_kind="other"
    )
    result = observed["metrics"]
    assert result["other_kind_count"] == 1
    assert result["count"] == 0
    assert result["measured"] is True
