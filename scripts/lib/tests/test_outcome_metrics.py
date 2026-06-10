"""アウトカム3軸（correction 再発率 / 一発成功率 / rework 率）のテスト（#423）。

決定論・LLM 非依存。tmp の DATA_DIR に疑似 jsonl ストアを置いて算出する。
各軸は (value, evidence) を返し、データ不足時は value=None で「データ不足」を明示する。

monkeypatch は文字列ターゲットを避け、import した module オブジェクトを直接 patch する
（order-dependent 失敗の既知 pitfall 準拠）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import outcome_metrics  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


# ---------- correction 再発率 ----------

class TestCorrectionRecurrence:
    def test_no_corrections_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_recurrence_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        # 同 correction_type "iya" が 2 つの異なるセッションで発生 → 再発
        # "stop" は 1 セッションのみ → 非再発
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": _iso(now - timedelta(days=2))},
            {"correction_type": "iya", "session_id": "s2", "timestamp": _iso(now - timedelta(days=1))},
            {"correction_type": "stop", "session_id": "s3", "timestamp": _iso(now)},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        # 2 distinct types, 1 recurring → 0.5
        assert value == pytest.approx(0.5)
        assert evidence["distinct_types"] == 2
        assert evidence["recurring_types"] == 1
        assert "iya" in evidence["examples"]

    def test_window_filters_old(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        records = [
            {"correction_type": "iya", "session_id": "s1", "timestamp": _iso(now - timedelta(days=100))},
            {"correction_type": "iya", "session_id": "s2", "timestamp": _iso(now - timedelta(days=99))},
        ]
        _write_jsonl(tmp_path / "corrections.jsonl", records)
        value, evidence = outcome_metrics.correction_recurrence_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"


# ---------- 一発成功率 ----------

class TestFirstTrySuccess:
    def test_no_sessions_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_mixed_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        records = [
            {"session_id": "s1", "error_count": 0, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "error_count": 0, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s3", "error_count": 3, "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s4", "error_count": 1, "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        # 2 of 4 sessions error-free
        assert value == pytest.approx(0.5)
        assert evidence["total_sessions"] == 4
        assert evidence["clean_sessions"] == 2

    def test_window_filters_old(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        old = _iso(now - timedelta(days=100))
        records = [{"session_id": "s1", "error_count": 0, "timestamp": old, "first_timestamp": old}]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.first_try_success_rate(days=30)
        assert value is None


# ---------- rework 率 ----------

class TestReworkRate:
    def test_no_sessions_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        value, evidence = outcome_metrics.rework_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"

    def test_edit_burst_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # s1: 3 連続 Edit（検証ツール介在なし）→ rework
        # s2: Edit → Bash → Edit（介在あり）→ rework でない
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Edit", "Edit", "Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "tool_sequence": ["Edit", "Bash", "Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30, min_consecutive=3)
        # 1 of 2 sessions with an edit-burst >= 3
        assert value == pytest.approx(0.5)
        assert evidence["total_sessions"] == 2
        assert evidence["rework_sessions"] == 1
        assert "s1" in evidence["examples"]

    def test_no_edit_sessions_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # 編集を 1 度も行わないセッションは分母から除外（rework の母集団は編集ありセッション）
        records = [
            {"session_id": "s1", "tool_sequence": ["Read", "Bash", "Bash"], "timestamp": ts, "first_timestamp": ts},
        ]
        _write_jsonl(tmp_path / "sessions.jsonl", records)
        value, evidence = outcome_metrics.rework_rate(days=30)
        assert value is None
        assert evidence["reason"] == "no_data"


# ---------- builder（observability） ----------

class TestSectionBuilder:
    def test_returns_lines_with_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        _write_jsonl(tmp_path / "sessions.jsonl", [
            {"session_id": "s1", "error_count": 0, "tool_sequence": ["Read", "Edit", "Edit", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
            {"session_id": "s2", "error_count": 2, "tool_sequence": ["Edit", "Bash"],
             "timestamp": ts, "first_timestamp": ts},
        ])
        _write_jsonl(tmp_path / "corrections.jsonl", [
            {"correction_type": "iya", "session_id": "s1", "timestamp": ts},
            {"correction_type": "iya", "session_id": "s2", "timestamp": ts},
        ])
        from audit.sections_outcome import build_outcome_metrics_section

        lines = build_outcome_metrics_section(tmp_path)
        assert lines is not None
        joined = "\n".join(lines)
        assert "advisory" in joined.lower()
        # evidence（件数）が含まれる
        assert "session" in joined.lower() or "セッション" in joined

    def test_all_no_data_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        from audit.sections_outcome import build_outcome_metrics_section

        # 該当ストアが 1 つも無い環境（評価対象なし）は沈黙（None）。
        # orphan_store / hook_drift と同じ「評価対象が無ければ沈黙」の境界。
        lines = build_outcome_metrics_section(tmp_path)
        assert lines is None

    def test_partial_data_shows_data_insufficient_for_empty_axis(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)
        now = _now()
        ts = _iso(now)
        # sessions のみ存在 → first_try/rework は算出、correction はデータ不足を明示
        _write_jsonl(tmp_path / "sessions.jsonl", [
            {"session_id": "s1", "error_count": 0, "tool_sequence": ["Read", "Edit"],
             "timestamp": ts, "first_timestamp": ts},
        ])
        from audit.sections_outcome import build_outcome_metrics_section

        lines = build_outcome_metrics_section(tmp_path)
        assert lines is not None
        joined = "\n".join(lines)
        assert "データ不足" in joined  # correction 軸
        assert "advisory" in joined.lower()
