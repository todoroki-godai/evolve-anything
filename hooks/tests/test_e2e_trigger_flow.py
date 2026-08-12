"""E2E: session_summary → trigger_engine → pending-trigger.json → restore_state → メッセージ出力。"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

_hooks_dir = Path(__file__).resolve().parent.parent
_plugin_root = _hooks_dir.parent
sys.path.insert(0, str(_hooks_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import trigger_engine
import session_store
import restore_state


@pytest.fixture
def data_dir(tmp_path):
    with mock.patch("trigger_engine.DATA_DIR", tmp_path), mock.patch(
        "trigger_engine.EVOLVE_STATE_FILE", tmp_path / "evolve-state.json"
    ), mock.patch(
        "trigger_engine.PENDING_TRIGGER_FILE", tmp_path / "pending-trigger.json"
    ), mock.patch(
        "trigger_engine.SNOOZE_FILE", tmp_path / "trigger-snooze.json"
    ), mock.patch.object(
        session_store, "_DATA_DIR_OVERRIDE", tmp_path
    ):
        yield tmp_path


def _silence_other_notifications(monkeypatch):
    """pending_trigger 以外の収集関数を無効化し、E2E テストのノイズを排除する
    （ADR-054 Phase 0）。ambient な実環境状態（marker root 等）に依存させない。"""
    for name in (
        "_build_spec_drift_output",
        "_build_evolve_drain_output",
        "_build_data_dir_migration_output",
        "_build_utterance_staleness_output",
        "_build_evolve_queue_output",
        "_build_session_proposal_output",
        "_build_judge_cap_output",
        "_build_icebox_output",
    ):
        monkeypatch.setattr(restore_state, name, lambda *a, **k: None)


class TestE2ETriggerFlow:
    def test_full_flow_session_end(self, data_dir):
        """session_summary → trigger_engine → pending → restore_state の一連フロー。"""
        # Step 1: Setup evolve state (8 days ago, recent audit)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        state = {
            "last_run_timestamp": old_ts,
            "last_audit_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "evolve-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        # Step 2: Evaluate session end (simulating session_summary hook)
        result = trigger_engine.evaluate_session_end()
        assert result.triggered is True
        assert "days_elapsed" in result.details.get("all_reasons", [])

        # Step 3: Write pending trigger (what session_summary._evaluate_trigger does)
        trigger_engine.write_pending_trigger(result)
        assert (data_dir / "pending-trigger.json").exists()

        # Step 4: Read and deliver (what restore_state._deliver_pending_trigger does)
        data = trigger_engine.read_and_delete_pending_trigger()
        assert data is not None
        assert data["triggered"] is True
        assert "/evolve-anything:evolve" in data["message"]

        # Step 5: File should be deleted after delivery
        assert not (data_dir / "pending-trigger.json").exists()

    def test_no_trigger_no_pending(self, data_dir):
        """条件未達 → pending-trigger.json なし → restore_state は何もしない。"""
        state = {
            "last_run_timestamp": datetime.now(timezone.utc).isoformat(),
            "last_audit_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "evolve-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        result = trigger_engine.evaluate_session_end()
        assert result.triggered is False

        # No pending file written
        assert not (data_dir / "pending-trigger.json").exists()

        # Restore state sees nothing
        data = trigger_engine.read_and_delete_pending_trigger()
        assert data is None

    def test_audit_overdue_triggers(self, data_dir):
        """audit overdue → /evolve-anything:audit を提案。"""
        state = {
            "last_run_timestamp": datetime.now(timezone.utc).isoformat(),
            # No last_audit_timestamp → overdue
        }
        (data_dir / "evolve-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        result = trigger_engine.evaluate_session_end()
        assert result.triggered is True
        assert "audit_overdue" in result.details.get("all_reasons", [])
        assert "/evolve-anything:audit" in result.message

    def test_corrections_flow(self, data_dir):
        """corrections 閾値到達 → メッセージ出力。"""
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

    def test_snooze_suppresses_delivery(self, data_dir):
        """スヌーズ中は pending-trigger を配信しない（ファイルは残る）。"""
        # pending-trigger を作成
        pending = {
            "triggered": True,
            "reason": "days_elapsed",
            "action": "/evolve-anything:evolve",
            "message": "前回 evolve から 15.6 日経過",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "pending-trigger.json").write_text(
            json.dumps(pending), encoding="utf-8"
        )

        # スヌーズ設定（24時間後まで）
        trigger_engine.snooze_trigger(hours=24)
        assert (data_dir / "trigger-snooze.json").exists()

        # 配信されない（None）+ ファイルが残る
        data = trigger_engine.read_and_delete_pending_trigger()
        assert data is None
        assert (data_dir / "pending-trigger.json").exists()

    def test_snooze_expired_delivers(self, data_dir):
        """スヌーズ期限切れなら通常通り配信する。"""
        pending = {
            "triggered": True,
            "reason": "days_elapsed",
            "action": "/evolve-anything:evolve",
            "message": "前回 evolve から 15.6 日経過",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "pending-trigger.json").write_text(
            json.dumps(pending), encoding="utf-8"
        )

        # 過去のスヌーズ（既に期限切れ）
        expired = {
            "snoozed_until": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        (data_dir / "trigger-snooze.json").write_text(
            json.dumps(expired), encoding="utf-8"
        )

        # 期限切れなので配信される
        data = trigger_engine.read_and_delete_pending_trigger()
        assert data is not None
        assert data["triggered"] is True
        # スヌーズファイルもクリーンアップ
        assert not (data_dir / "trigger-snooze.json").exists()

    def test_snooze_clears_on_evolve_run(self, data_dir):
        """evolve 実行でスヌーズが自動解除される。"""
        trigger_engine.snooze_trigger(hours=48)
        assert (data_dir / "trigger-snooze.json").exists()

        trigger_engine.clear_snooze()
        assert not (data_dir / "trigger-snooze.json").exists()

    def test_cooldown_prevents_repeated_trigger(self, data_dir):
        """クールダウン中は同一条件の再トリガーを防止。"""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        state = {
            "last_run_timestamp": old_ts,
            "last_audit_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "evolve-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        # First trigger fires
        r1 = trigger_engine.evaluate_session_end()
        assert r1.triggered is True

        # Second trigger is blocked by cooldown
        r2 = trigger_engine.evaluate_session_end()
        assert r2.triggered is False


class TestPendingTriggerAckFlow:
    """ADR-054 Phase 0 §5.5/§7.1-8: pending_trigger を ack 方式（collect→print→commit）で
    restore_state.handle_session_start 経由で検証する。read_and_delete と違い、
    print が成功するまでファイルは削除されない。
    """

    def _write_pending(self, data_dir: Path) -> dict:
        pending = {
            "triggered": True,
            "reason": "days_elapsed",
            "action": "/evolve-anything:evolve",
            "message": "前回 evolve から 15.6 日経過",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (data_dir / "pending-trigger.json").write_text(json.dumps(pending), encoding="utf-8")
        return pending

    def test_normal_path_prints_and_deletes(self, data_dir, monkeypatch, capsys):
        """正常経路: メッセージが最終1行に含まれ、ファイルが削除される。"""
        _silence_other_notifications(monkeypatch)
        self._write_pending(data_dir)

        restore_state.handle_session_start({})

        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 1
        assert "前回 evolve から 15.6 日経過" in lines[0]
        assert not (data_dir / "pending-trigger.json").exists()

    def test_failure_path_builder_exception_keeps_file(self, data_dir, monkeypatch, capsys):
        """失敗経路①: 収集関数自身の例外 → ファイルは削除されない（次回セッションで再候補）。"""
        _silence_other_notifications(monkeypatch)
        self._write_pending(data_dir)

        def _boom():
            raise RuntimeError("boom in peek")

        # 実装は scripts/lib/session_notify/collectors.py に分割済み（ADR-054 Phase 0）。
        # _build_pending_trigger_output は peek_pending_trigger をそのモジュールの
        # globals で bare name 解決するため、patch 対象もそちらにする（import パス追従）。
        from session_notify import collectors as _sn_collectors

        monkeypatch.setattr(_sn_collectors, "peek_pending_trigger", _boom)

        restore_state.handle_session_start({})

        assert (data_dir / "pending-trigger.json").exists()

    def test_failure_path_merge_exception_keeps_file(self, data_dir, monkeypatch, capsys):
        """失敗経路②: merge 関数の例外 → commit されず、ファイルは削除されない。"""
        _silence_other_notifications(monkeypatch)
        self._write_pending(data_dir)

        def _boom(items):
            raise RuntimeError("boom in merge")

        monkeypatch.setattr(restore_state, "_merge_notification_text", _boom)

        restore_state.handle_session_start({})

        assert (data_dir / "pending-trigger.json").exists()
        err = capsys.readouterr().err
        assert "merge/print failed" in err

    def test_failure_path_print_exception_keeps_file(self, data_dir, monkeypatch, capsys):
        """失敗経路③: print/json.dumps 自体の失敗 → commit されず、ファイルは削除されない。"""
        _silence_other_notifications(monkeypatch)
        self._write_pending(data_dir)

        def _boom(*a, **k):
            raise TypeError("not serializable")

        monkeypatch.setattr(restore_state.json, "dumps", _boom)

        restore_state.handle_session_start({})

        assert (data_dir / "pending-trigger.json").exists()
