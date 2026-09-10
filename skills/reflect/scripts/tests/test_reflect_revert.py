"""#637 reflect CLI の柱2取り消し遷移テスト。"""
import json
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest


_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "skills" / "reflect" / "scripts"))

import reflect
import rl_common
from reflect_fold import _hash_correction_message
from rl_common.persistence import WriteResult


BASE_ID = "a" * 32
ATTEMPT_1 = "b" * 32
APPLIED_1 = "c" * 32
ATTEMPT_2 = "d" * 32
APPLIED_2 = "e" * 32


def _base():
    return {
        "correction_id": BASE_ID,
        "extracted_learning": "Use the stable API",
        "reflect_status": "applied",
        "project_path": None,
    }


def _attempt(correction_id=ATTEMPT_1, *, at="2026-09-01T10:00:00+00:00"):
    return {
        "correction_id": correction_id,
        "schema_version": 1,
        "event_type": "correction_apply_attempted",
        "target_correction_id": BASE_ID,
        "reflect_target_kind": "project_rule",
        "reflect_target_path": "repo:.claude/rules/a.md",
        "reflect_draft_line": "Use the stable API",
        "correction_message_sha256": _hash_correction_message(_base()),
        "attempted_at": at,
    }


def _applied(
    correction_id=APPLIED_1,
    *,
    attempt_id=ATTEMPT_1,
    at="2026-09-01T10:01:00+00:00",
):
    return {
        "correction_id": correction_id,
        "schema_version": 1,
        "event_type": "correction_applied",
        "target_correction_id": BASE_ID,
        "confirms_attempt_id": attempt_id,
        "reflect_applied_at": at,
    }


def _write(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _seed(tmp_path):
    corrections = tmp_path / "corrections.jsonl"
    events = rl_common.DATA_DIR / "reflect_apply_events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    _write(corrections, [_base()])
    _write(events, [_attempt(), _applied()])
    return corrections, events


def _run_cli(*args):
    with mock.patch("sys.argv", ["reflect.py", *args]):
        reflect.main()


def test_list_applied_is_read_only_and_includes_revoke_identifiers(tmp_path, capsys):
    corrections, events = _seed(tmp_path)
    before = events.read_bytes()

    _run_cli("--list-applied", "--corrections-file", str(corrections))

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "status": "applied-list",
        "applied": [
            {
                "applied_id": APPLIED_1,
                "target_path": "repo:.claude/rules/a.md",
                "reflect_applied_at": "2026-09-01T10:01:00+00:00",
            }
        ],
    }
    assert events.read_bytes() == before
    assert not events.with_name(events.name + ".lock").exists()


def test_revoke_appends_event_and_removes_applied_from_list(tmp_path, capsys):
    corrections, events = _seed(tmp_path)

    _run_cli(
        "--revoke",
        APPLIED_1,
        "--reason",
        "The reflected change was reverted",
        "--corrections-file",
        str(corrections),
    )

    output = json.loads(capsys.readouterr().out)
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert output["status"] == "revoked"
    assert output["reverts_applied_id"] == APPLIED_1
    assert rows[-1]["event_type"] == "correction_reverted"
    assert rows[-1]["revert_reason"] == "The reflected change was reverted"

    _run_cli("--list-applied", "--corrections-file", str(corrections))
    assert json.loads(capsys.readouterr().out)["applied"] == []


def test_revoke_race_with_newer_apply_writes_nothing(tmp_path, monkeypatch):
    corrections, events = _seed(tmp_path)
    entered_append = threading.Event()
    newer_written = threading.Event()
    original_append = reflect.persistence.append_jsonl

    def delayed_append(*args, **kwargs):
        entered_append.set()
        assert newer_written.wait(timeout=5)
        return original_append(*args, **kwargs)

    monkeypatch.setattr(reflect.persistence, "append_jsonl", delayed_append)

    writer_result = []

    def write_newer():
        assert entered_append.wait(timeout=5)
        writer_result.extend(
            [
                original_append(
                    events,
                    _attempt(ATTEMPT_2, at="2026-09-01T11:00:00+00:00"),
                    duplicate_check=lambda _: False,
                ),
                original_append(
                    events,
                    _applied(
                        APPLIED_2,
                        attempt_id=ATTEMPT_2,
                        at="2026-09-01T11:01:00+00:00",
                    ),
                    duplicate_check=lambda _: False,
                ),
            ]
        )
        newer_written.set()

    writer = threading.Thread(target=write_newer)
    writer.start()
    result = reflect.revoke_applied(corrections, APPLIED_1, "race test")
    writer.join(timeout=5)

    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert [item.status for item in writer_result] == ["written", "written"]
    assert result["status"] == "retry-required"
    assert [row["event_type"] for row in rows].count("correction_reverted") == 0


def test_revoke_rechecks_inside_append_before_deciding(tmp_path, monkeypatch):
    corrections, _ = _seed(tmp_path)
    existing = [_attempt(), _applied()]
    calls = []
    original_fold = reflect.fold_corrections

    def traced_fold(*args, **kwargs):
        calls.append("recheck-fold")
        return original_fold(*args, **kwargs)

    def fake_append(path, record, *, duplicate_check):
        calls.append("append-enter")
        blocked = duplicate_check(existing)
        calls.append("append-blocked" if blocked else "append-write")
        return WriteResult(status="duplicate" if blocked else "written")

    monkeypatch.setattr(reflect, "fold_corrections", traced_fold)
    monkeypatch.setattr(reflect.persistence, "append_jsonl", fake_append)

    result = reflect.revoke_applied(corrections, "1" * 32, "stale selection")

    assert result["status"] == "retry-required"
    assert calls == ["append-enter", "recheck-fold", "append-blocked"]


@pytest.mark.parametrize(
    "other_action",
    ["--apply", "--skip", "--promote-weak", "--reject-weak", "--already-reflected-weak"],
)
def test_revoke_is_mutually_exclusive_with_existing_write_actions(
    tmp_path, other_action, capsys
):
    corrections, _ = _seed(tmp_path)
    with mock.patch(
        "sys.argv",
        [
            "reflect.py",
            "--revoke",
            APPLIED_1,
            "--reason",
            "reason",
            other_action,
            "other-id",
            "--corrections-file",
            str(corrections),
        ],
    ), pytest.raises(SystemExit) as exc:
        reflect.main()

    assert exc.value.code == 2
    assert "not allowed with argument --revoke" in capsys.readouterr().err


@pytest.mark.parametrize("reason", ["", "line one\nline two"])
def test_revoke_requires_nonempty_single_line_reason(tmp_path, capsys, reason):
    corrections, events = _seed(tmp_path)
    before = events.read_bytes()

    with pytest.raises(SystemExit) as exc:
        _run_cli(
            "--revoke",
            APPLIED_1,
            "--reason",
            reason,
            "--corrections-file",
            str(corrections),
        )

    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid-request"
    assert events.read_bytes() == before
