"""memory_trace のテスト — TDD 正常系→異常系の順。

memory_trace.attribute_errors() の入力:
  - events: query_relevant 形式の dict リスト
      {id, content, correction_type, timestamp, days_ago, score}
  - temporals: {event_id: temporal_dict} (parse_memory_temporal の返り値形式)
  - corrections: corrections.jsonl の各行 dict のリスト
      {timestamp, session_id, ...}  (reflect_status は任意)
  - score_threshold: float (デフォルト 0.3)
  - staleness_days: int (デフォルト 30)
  - post_retrieval_window_sec: int (デフォルト 300)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib_dir))

import memory_trace as mt


# ─── ヘルパー ──────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_event(
    event_id: str = "sess1#2024-01-01T00:00:00+00:00",
    score: float = 0.8,
    days_ago: int = 0,
    content: str = "git diff で変更確認",
    correction_type: str | None = None,
    timestamp: str | None = None,
) -> dict:
    ts = timestamp or _iso(_utcnow() - timedelta(days=days_ago))
    return {
        "id": event_id,
        "content": content,
        "correction_type": correction_type,
        "timestamp": ts,
        "days_ago": days_ago,
        "score": score,
    }


def _make_temporal(
    decay_days: int | None = None,
    valid_from: str | None = None,
) -> dict:
    return {
        "valid_from": valid_from,
        "superseded_at": None,
        "decay_days": decay_days,
        "source_correction_ids": [],
        "update_count": 0,
        "importance_score": 0.5,
        "last_reinforced_at": None,
    }


# ─── 正常系 E2E ────────────────────────────────────────────────────────────────

class TestAttributeErrors_E2E:
    """3エラー類型が正しく分類され event_id に帰属する E2E テスト。"""

    def test_misretrieval_detected(self):
        """score が score_threshold 未満のイベントは misretrieval として帰属する。"""
        event_id = "sess1#2024-01-01T00:00:00+00:00"
        events = [_make_event(event_id=event_id, score=0.1)]  # 閾値 0.3 未満
        result = mt.attribute_errors(events, {}, [], score_threshold=0.3)

        misretrievals = [e for e in result if e["error_type"] == "misretrieval"]
        assert len(misretrievals) == 1
        assert misretrievals[0]["event_id"] == event_id
        assert misretrievals[0]["signal"] == "low_score"
        assert "score" in misretrievals[0]["detail"]

    def test_context_drift_detected(self):
        """decay_days を超過している temporal は context_drift として帰属する。"""
        event_id = "sess2#2024-01-01T00:00:00+00:00"
        old_valid_from = _iso(_utcnow() - timedelta(days=60))
        temporal = _make_temporal(decay_days=30, valid_from=old_valid_from)
        events = [_make_event(event_id=event_id, score=0.9)]  # score は十分
        result = mt.attribute_errors(events, {event_id: temporal}, [], staleness_days=30)

        drifts = [e for e in result if e["error_type"] == "context_drift"]
        assert len(drifts) == 1
        assert drifts[0]["event_id"] == event_id
        assert drifts[0]["signal"] == "stale_temporal"

    def test_corruption_detected(self):
        """検索直後 (post_retrieval_window_sec 以内) に correction が発生した場合は corruption。"""
        event_id = "sess3#2024-01-01T00:00:00+00:00"
        retrieval_ts = _utcnow() - timedelta(minutes=1)
        event = _make_event(
            event_id=event_id,
            score=0.9,
            timestamp=_iso(retrieval_ts),
        )
        # 検索から 2 分後（window=300秒以内）に correction
        correction_ts = retrieval_ts + timedelta(minutes=2)
        corrections = [{"timestamp": _iso(correction_ts), "session_id": "sess3"}]

        result = mt.attribute_errors(
            [event], {}, corrections,
            post_retrieval_window_sec=300,
        )
        corruptions = [e for e in result if e["error_type"] == "corruption"]
        assert len(corruptions) == 1
        assert corruptions[0]["event_id"] == event_id
        assert corruptions[0]["signal"] == "post_retrieval_correction"

    def test_all_three_error_types_combined(self):
        """3種類のエラーが同時に検出できる。"""
        old_valid_from = _iso(_utcnow() - timedelta(days=60))
        retrieval_ts = _utcnow() - timedelta(minutes=1)

        e_misretrieval = _make_event("ev1", score=0.1)
        e_drift = _make_event(
            "ev2",
            score=0.9,
            timestamp=_iso(_utcnow() - timedelta(days=5)),
        )
        e_corruption = _make_event(
            "ev3",
            score=0.9,
            timestamp=_iso(retrieval_ts),
        )

        temporals = {
            "ev2": _make_temporal(decay_days=30, valid_from=old_valid_from),
        }
        corrections = [
            {"timestamp": _iso(retrieval_ts + timedelta(minutes=2)), "session_id": "x"},
        ]

        result = mt.attribute_errors(
            [e_misretrieval, e_drift, e_corruption],
            temporals,
            corrections,
            score_threshold=0.3,
            staleness_days=30,
            post_retrieval_window_sec=300,
        )

        types = {e["error_type"] for e in result}
        assert "misretrieval" in types
        assert "context_drift" in types
        assert "corruption" in types

    def test_result_schema(self):
        """返り値の各エントリが {event_id, error_type, signal, detail} を持つ。"""
        event_id = "ev_schema"
        events = [_make_event(event_id=event_id, score=0.05)]
        result = mt.attribute_errors(events, {}, [])
        assert len(result) >= 1
        for entry in result:
            assert "event_id" in entry
            assert "error_type" in entry
            assert "signal" in entry
            assert "detail" in entry


# ─── 異常系 ────────────────────────────────────────────────────────────────────

class TestAttributeErrors_EdgeCases:
    def test_no_duckdb_flag_returns_empty(self, monkeypatch):
        """HAS_DUCKDB=False の場合は空リストを返す。"""
        monkeypatch.setattr(mt, "HAS_DUCKDB", False)
        result = mt.attribute_errors([], {}, [])
        assert result == []

    def test_no_signals_returns_empty(self):
        """エラーシグナルが一切ない場合は空リストを返す。"""
        # score 十分、temporal なし、correction なし
        events = [_make_event(score=0.9)]
        result = mt.attribute_errors(events, {}, [])
        assert result == []

    def test_empty_events_returns_empty(self):
        """events が空の場合は空リストを返す。"""
        result = mt.attribute_errors([], {}, [])
        assert result == []

    def test_timestamp_missing_in_event(self):
        """timestamp が空文字でも例外を投げない。"""
        events = [_make_event(event_id="ev_notimestamp", score=0.05, timestamp="")]
        result = mt.attribute_errors(events, {}, [])
        # misretrieval は検出される（score が低い）、例外なし
        assert isinstance(result, list)

    def test_timestamp_missing_in_correction(self):
        """correction に timestamp がなくても例外を投げない。"""
        events = [_make_event(event_id="ev1", score=0.9)]
        corrections = [{"session_id": "s1"}]  # timestamp なし
        result = mt.attribute_errors(events, {}, corrections)
        assert isinstance(result, list)

    def test_misretrieval_not_flagged_above_threshold(self):
        """score が閾値以上なら misretrieval を検出しない。"""
        events = [_make_event(score=0.5)]
        result = mt.attribute_errors(events, {}, [], score_threshold=0.3)
        misretrievals = [e for e in result if e["error_type"] == "misretrieval"]
        assert len(misretrievals) == 0

    def test_context_drift_not_flagged_without_decay_days(self):
        """decay_days が None の temporal は context_drift を検出しない。"""
        event_id = "ev_nodecay"
        temporal = _make_temporal(decay_days=None)
        events = [_make_event(event_id=event_id, score=0.9)]
        result = mt.attribute_errors(events, {event_id: temporal}, [])
        drifts = [e for e in result if e["error_type"] == "context_drift"]
        assert len(drifts) == 0

    def test_corruption_not_flagged_outside_window(self):
        """correction が window 外なら corruption を検出しない。"""
        retrieval_ts = _utcnow() - timedelta(hours=2)
        event = _make_event(event_id="ev_outwindow", score=0.9, timestamp=_iso(retrieval_ts))
        # window 10 秒、correction は 10 分後
        correction_ts = retrieval_ts + timedelta(minutes=10)
        corrections = [{"timestamp": _iso(correction_ts), "session_id": "x"}]
        result = mt.attribute_errors(
            [event], {}, corrections, post_retrieval_window_sec=10
        )
        corruptions = [e for e in result if e["error_type"] == "corruption"]
        assert len(corruptions) == 0


# ─── build_memory_trace_section ───────────────────────────────────────────────

class TestBuildMemoryTraceSection:
    def test_empty_errors_returns_empty(self):
        lines = mt.build_memory_trace_section([])
        assert lines == []

    def test_non_empty_errors_returns_section(self):
        errors = [
            {
                "event_id": "ev1",
                "error_type": "misretrieval",
                "signal": "low_score",
                "detail": {"score": 0.05},
            }
        ]
        lines = mt.build_memory_trace_section(errors)
        assert any("Memory Trace" in line for line in lines)
        assert any("misretrieval" in line for line in lines)

    def test_section_groups_by_error_type(self):
        errors = [
            {"event_id": "ev1", "error_type": "misretrieval", "signal": "low_score", "detail": {}},
            {"event_id": "ev2", "error_type": "context_drift", "signal": "stale_temporal", "detail": {}},
        ]
        lines = mt.build_memory_trace_section(errors)
        text = "\n".join(lines)
        assert "misretrieval" in text
        assert "context_drift" in text
