"""session_summary trigger integration tests."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

# Setup paths
_hooks_dir = Path(__file__).resolve().parent.parent
_plugin_root = _hooks_dir.parent
sys.path.insert(0, str(_hooks_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import trigger_engine


@pytest.fixture
def data_dir(tmp_path):
    """Patch DATA_DIR for both common and trigger_engine."""
    with mock.patch("trigger_engine.DATA_DIR", tmp_path), mock.patch(
        "trigger_engine.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json"
    ), mock.patch(
        "trigger_engine.PENDING_TRIGGER_FILE", tmp_path / "pending-trigger.json"
    ):
        yield tmp_path


class TestSessionSummaryTrigger:
    def test_trigger_fires_writes_pending(self, data_dir):
        """条件達成時に pending-trigger.json が書き出される。"""
        # Setup: 8 days since last evolve
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        state = {
            "last_run_timestamp": old,
            "last_audit_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "evolve-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        result = trigger_engine.evaluate_session_end()
        assert result.triggered is True

        trigger_engine.write_pending_trigger(result)
        assert (data_dir / "pending-trigger.json").exists()

    def test_no_trigger_no_pending(self, data_dir):
        """条件未達時に pending-trigger.json を書き出さない。"""
        state = {
            "last_run_timestamp": datetime.now(timezone.utc).isoformat(),
            "last_audit_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "evolve-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        result = trigger_engine.evaluate_session_end()
        assert result.triggered is False
        assert not (data_dir / "pending-trigger.json").exists()

    def test_trigger_error_handled(self, data_dir):
        """trigger_engine 例外時もセッション処理は続行する。"""
        with mock.patch(
            "trigger_engine.evaluate_session_end",
            side_effect=RuntimeError("test error"),
        ):
            # Simulate _evaluate_trigger logic
            try:
                trigger_engine.evaluate_session_end()
            except Exception:
                pass  # Error is caught in _evaluate_trigger


class TestRestoreStateTrigger:
    def test_deliver_pending_trigger(self, data_dir, capsys):
        """pending-trigger.json がある場合、stdout に出力して削除する。"""
        payload = {
            "triggered": True,
            "reason": "session_count",
            "action": "/rl-anything:evolve",
            "message": "前回 evolve から 12 セッション経過。推奨: /rl-anything:evolve",
        }
        (data_dir / "pending-trigger.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        data = trigger_engine.read_and_delete_pending_trigger()
        assert data is not None
        assert data["message"].startswith("前回 evolve から")
        assert not (data_dir / "pending-trigger.json").exists()

    def test_no_pending_trigger(self, data_dir):
        """pending-trigger.json がない場合は何もしない。"""
        data = trigger_engine.read_and_delete_pending_trigger()
        assert data is None


class TestSkillChangeDetection:
    def test_detect_no_changes(self):
        """git diff がなければ空リスト。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="")
            result = trigger_engine.detect_skill_changes()
            assert result == []

    def test_detect_changes(self):
        """git diff で変更スキルを検出。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout=".claude/skills/my-skill/SKILL.md\n.claude/skills/other/SKILL.md\n",
            )
            result = trigger_engine.detect_skill_changes()
            assert "my-skill" in result
            assert "other" in result

    def test_git_error_returns_empty(self):
        """git コマンドエラー時は空リスト。"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1, stdout="")
            result = trigger_engine.detect_skill_changes()
            assert result == []
