"""trigger_engine のユニットテスト。"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trigger_engine import (
    DEFAULT_TRIGGER_CONFIG,
    TriggerResult,
    _count_sessions_since,
    _deep_merge,
    _is_in_cooldown,
    _record_trigger,
    evaluate_corrections,
    evaluate_session_end,
    load_trigger_config,
    read_and_delete_pending_trigger,
    write_pending_trigger,
)


@pytest.fixture
def data_dir(tmp_path):
    """DATA_DIR を tmp_path に差し替える。"""
    with mock.patch("trigger_engine.DATA_DIR", tmp_path), mock.patch(
        "trigger_engine.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json"
    ), mock.patch(
        "trigger_engine.PENDING_TRIGGER_FILE", tmp_path / "pending-trigger.json"
    ):
        yield tmp_path


def _write_state(data_dir: Path, state: dict) -> None:
    (data_dir / "evolve-state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def _write_sessions(data_dir: Path, records: list[dict]) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (data_dir / "sessions.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _write_corrections(data_dir: Path, records: list[dict]) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (data_dir / "corrections.jsonl").write_text("\n".join(lines), encoding="utf-8")


# --- load_trigger_config ---


class TestLoadTriggerConfig:
    def test_default_when_no_config(self, data_dir):
        config = load_trigger_config({})
        assert config["enabled"] is True
        assert config["triggers"]["session_end"]["min_sessions"] == 10

    def test_user_override(self, data_dir):
        state = {"trigger_config": {"triggers": {"session_end": {"min_sessions": 5}}}}
        config = load_trigger_config(state)
        assert config["triggers"]["session_end"]["min_sessions"] == 5
        # Other defaults preserved
        assert config["triggers"]["session_end"]["max_days"] == 7
        assert config["cooldown_hours"] == 24

    def test_disabled(self, data_dir):
        state = {"trigger_config": {"enabled": False}}
        config = load_trigger_config(state)
        assert config["enabled"] is False


# --- Cooldown ---


class TestCooldown:
    def test_within_cooldown(self):
        now = datetime.now(timezone.utc)
        state = {
            "trigger_history": [
                {
                    "reason": "session_count",
                    "timestamp": now.isoformat(),
                }
            ]
        }
        assert _is_in_cooldown(state, "session_count", 24) is True

    def test_cooldown_expired(self):
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        state = {
            "trigger_history": [
                {
                    "reason": "session_count",
                    "timestamp": old.isoformat(),
                }
            ]
        }
        assert _is_in_cooldown(state, "session_count", 24) is False

    def test_different_reason_not_affected(self):
        now = datetime.now(timezone.utc)
        state = {
            "trigger_history": [
                {"reason": "days_elapsed", "timestamp": now.isoformat()}
            ]
        }
        assert _is_in_cooldown(state, "session_count", 24) is False


# --- History recording and pruning ---


class TestHistoryRecording:
    def test_record_trigger(self):
        state = {"trigger_history": []}
        result = TriggerResult(
            triggered=True, reason="session_count", action="/rl-anything:evolve"
        )
        state = _record_trigger(state, result)
        assert len(state["trigger_history"]) == 1
        assert state["trigger_history"][0]["reason"] == "session_count"

    def test_pruning(self):
        state = {
            "trigger_history": [
                {"reason": "x", "timestamp": "2025-01-01T00:00:00+00:00"}
            ]
            * 105
        }
        result = TriggerResult(triggered=True, reason="y", action="test")
        state = _record_trigger(state, result)
        assert len(state["trigger_history"]) <= 100


# --- evaluate_session_end ---


class TestEvaluateSessionEnd:
    def test_session_count_threshold(self, data_dir):
        last_run = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _write_state(data_dir, {"last_run_timestamp": last_run})
        # Write 10 sessions after last_run
        sessions = [
            {
                "session_id": f"s{i}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(10)
        ]
        _write_sessions(data_dir, sessions)

        result = evaluate_session_end()
        assert result.triggered is True
        assert "session_count" in result.details.get("all_reasons", [])

    def test_days_elapsed_threshold(self, data_dir):
        last_run = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        _write_state(data_dir, {"last_run_timestamp": last_run})

        result = evaluate_session_end()
        assert result.triggered is True
        assert "days_elapsed" in result.details.get("all_reasons", [])

    def test_no_trigger(self, data_dir):
        last_run = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _write_state(
            data_dir,
            {
                "last_run_timestamp": last_run,
                "last_audit_timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        result = evaluate_session_end()
        assert result.triggered is False

    def test_first_run_lower_threshold(self, data_dir):
        """evolve-state.json が空の場合、min_sessions=3 で判定。"""
        sessions = [
            {
                "session_id": f"s{i}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            for i in range(3)
        ]
        _write_sessions(data_dir, sessions)

        result = evaluate_session_end(state={})
        assert result.triggered is True

    def test_disabled_config(self, data_dir):
        state = {"trigger_config": {"enabled": False}}
        _write_state(data_dir, state)

        result = evaluate_session_end(state)
        assert result.triggered is False

    def test_audit_overdue_no_previous(self, data_dir):
        """last_audit_timestamp がない場合は audit overdue。"""
        _write_state(data_dir, {"last_run_timestamp": datetime.now(timezone.utc).isoformat()})

        result = evaluate_session_end()
        assert result.triggered is True
        assert "audit_overdue" in result.details.get("all_reasons", [])

    def test_audit_overdue_expired(self, data_dir):
        old_audit = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        _write_state(
            data_dir,
            {
                "last_run_timestamp": datetime.now(timezone.utc).isoformat(),
                "last_audit_timestamp": old_audit,
            },
        )

        result = evaluate_session_end()
        assert result.triggered is True
        assert "audit_overdue" in result.details.get("all_reasons", [])

    def test_audit_not_overdue(self, data_dir):
        recent_audit = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        _write_state(
            data_dir,
            {
                "last_run_timestamp": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
                "last_audit_timestamp": recent_audit,
            },
        )

        result = evaluate_session_end()
        assert result.triggered is False


# --- evaluate_corrections ---


class TestEvaluateCorrections:
    def test_threshold_reached(self, data_dir):
        _write_state(data_dir, {"last_run_timestamp": "2025-01-01T00:00:00+00:00"})
        corrections = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "last_skill": "my-skill",
            }
            for _ in range(10)
        ]
        _write_corrections(data_dir, corrections)

        result = evaluate_corrections()
        assert result.triggered is True
        assert result.reason == "corrections_threshold"
        assert "my-skill" in result.details.get("top_skills", [])

    def test_below_threshold(self, data_dir):
        _write_state(data_dir, {"last_run_timestamp": "2025-01-01T00:00:00+00:00"})
        corrections = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "last_skill": "s",
            }
            for _ in range(5)
        ]
        _write_corrections(data_dir, corrections)

        result = evaluate_corrections()
        assert result.triggered is False

    def test_no_skill_fallback(self, data_dir):
        _write_state(data_dir, {"last_run_timestamp": "2025-01-01T00:00:00+00:00"})
        corrections = [
            {"timestamp": datetime.now(timezone.utc).isoformat(), "last_skill": ""}
            for _ in range(10)
        ]
        _write_corrections(data_dir, corrections)

        result = evaluate_corrections()
        assert result.triggered is True
        assert result.action == "/rl-anything:evolve"

    def test_cooldown_blocks(self, data_dir):
        now = datetime.now(timezone.utc)
        _write_state(
            data_dir,
            {
                "last_run_timestamp": "2025-01-01T00:00:00+00:00",
                "trigger_history": [
                    {
                        "reason": "corrections_threshold",
                        "timestamp": now.isoformat(),
                    }
                ],
            },
        )
        corrections = [
            {"timestamp": now.isoformat(), "last_skill": "s"} for _ in range(15)
        ]
        _write_corrections(data_dir, corrections)

        result = evaluate_corrections()
        assert result.triggered is False
        assert result.reason == "cooldown"

    def test_multiple_skills_top3(self, data_dir):
        _write_state(data_dir, {"last_run_timestamp": "2025-01-01T00:00:00+00:00"})
        now = datetime.now(timezone.utc).isoformat()
        corrections = []
        # skill-a: 5, skill-b: 3, skill-c: 2, skill-d: 1
        for skill, count in [("skill-a", 5), ("skill-b", 3), ("skill-c", 2), ("skill-d", 1)]:
            for _ in range(count):
                corrections.append({"timestamp": now, "last_skill": skill})
        _write_corrections(data_dir, corrections)

        result = evaluate_corrections()
        assert result.triggered is True
        top = result.details["top_skills"]
        assert len(top) == 3
        assert top[0] == "skill-a"


# --- Pending trigger file ---


class TestPendingTrigger:
    def test_write_and_read(self, data_dir):
        result = TriggerResult(
            triggered=True,
            reason="session_count",
            action="/rl-anything:evolve",
            message="test message",
        )
        write_pending_trigger(result)
        assert (data_dir / "pending-trigger.json").exists()

        data = read_and_delete_pending_trigger()
        assert data is not None
        assert data["reason"] == "session_count"
        assert not (data_dir / "pending-trigger.json").exists()

    def test_read_nonexistent(self, data_dir):
        assert read_and_delete_pending_trigger() is None

    def test_read_corrupt_file(self, data_dir):
        (data_dir / "pending-trigger.json").write_text("invalid json")
        assert read_and_delete_pending_trigger() is None
        assert not (data_dir / "pending-trigger.json").exists()
