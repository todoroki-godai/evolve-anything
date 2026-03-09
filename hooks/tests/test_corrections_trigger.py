"""correction_detect trigger integration tests."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

_hooks_dir = Path(__file__).resolve().parent.parent
_plugin_root = _hooks_dir.parent
sys.path.insert(0, str(_hooks_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import trigger_engine


@pytest.fixture
def data_dir(tmp_path):
    with mock.patch("trigger_engine.DATA_DIR", tmp_path), mock.patch(
        "trigger_engine.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json"
    ), mock.patch(
        "trigger_engine.PENDING_TRIGGER_FILE", tmp_path / "pending-trigger.json"
    ):
        yield tmp_path


class TestCorrectionsTriggerIntegration:
    def test_threshold_reached_outputs_message(self, data_dir):
        """閾値到達時に提案メッセージが生成される。"""
        (data_dir / "evolve-state.json").write_text(
            json.dumps({"last_run_timestamp": "2025-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        now = datetime.now(timezone.utc).isoformat()
        corrections = [
            json.dumps({"timestamp": now, "last_skill": "my-skill"})
            for _ in range(10)
        ]
        (data_dir / "corrections.jsonl").write_text(
            "\n".join(corrections), encoding="utf-8"
        )

        result = trigger_engine.evaluate_corrections()
        assert result.triggered is True
        assert "my-skill" in result.message

    def test_below_threshold_silent(self, data_dir):
        """閾値未達時はサイレント。"""
        (data_dir / "evolve-state.json").write_text(
            json.dumps({"last_run_timestamp": "2025-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        now = datetime.now(timezone.utc).isoformat()
        corrections = [
            json.dumps({"timestamp": now, "last_skill": "s"}) for _ in range(3)
        ]
        (data_dir / "corrections.jsonl").write_text(
            "\n".join(corrections), encoding="utf-8"
        )

        result = trigger_engine.evaluate_corrections()
        assert result.triggered is False

    def test_top3_skills_identified(self, data_dir):
        """複数スキルの場合、上位3件を特定。"""
        (data_dir / "evolve-state.json").write_text(
            json.dumps({"last_run_timestamp": "2025-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        now = datetime.now(timezone.utc).isoformat()
        corrections = []
        for skill, count in [("a", 5), ("b", 4), ("c", 3), ("d", 2)]:
            for _ in range(count):
                corrections.append(json.dumps({"timestamp": now, "last_skill": skill}))
        (data_dir / "corrections.jsonl").write_text(
            "\n".join(corrections), encoding="utf-8"
        )

        result = trigger_engine.evaluate_corrections()
        assert result.triggered is True
        assert len(result.details["top_skills"]) == 3
        assert result.details["top_skills"][0] == "a"
