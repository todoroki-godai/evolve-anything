"""compute_negative_transfer() のユニットテスト。

per-skill 負の転移測定（Issue #202）のテスト。
実際の usage.jsonl スキーマ（{skill_name, ts, session_id, outcome}）に対応。
"""
import pytest
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.usage import compute_negative_transfer


def _make_usage_data(events: list[dict]) -> list[dict]:
    """テスト用の usage_data を構築するヘルパー。"""
    return events


class TestComputeNegativeTransferBasic:
    """基本的な負の転移検出のテスト。"""

    def test_negative_transfer_detected_when_delta_below_threshold(self):
        """after - before < -0.05 のとき negative_transfer=True になること。

        既存スキル "ship" が before=1.0 (success/success)、after=0.0 (error/error) になる場合。
        """
        usage_data = [
            # 既存スキル ship（最初から存在）
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "session_id": "s1", "outcome": "success"},
            # 新規スキル review が追加される（2番目に登場）
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            # ship の after: error が続く
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "error"},
            {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "session_id": "s3", "outcome": "error"},
        ]
        results = compute_negative_transfer(usage_data)
        assert len(results) == 1
        r = results[0]
        assert r["skill_name"] == "ship"
        assert r["negative_transfer"] is True
        # before=1.0, after=0.0, delta=-1.0
        assert r["before_score"] == pytest.approx(1.0)
        assert r["after_score"] == pytest.approx(0.0)
        assert r["delta_score"] == pytest.approx(-1.0)

    def test_no_negative_transfer_when_delta_above_threshold(self):
        """after - before >= -0.05 のとき negative_transfer=False になること。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            # after も success が続く（低下なし）
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "session_id": "s3", "outcome": "success"},
        ]
        results = compute_negative_transfer(usage_data)
        assert len(results) == 1
        r = results[0]
        assert r["skill_name"] == "ship"
        assert r["negative_transfer"] is False
        assert r["delta_score"] == pytest.approx(0.0)

    def test_custom_threshold(self):
        """delta_threshold をカスタム値で指定できること。"""
        # before=1.0 (2 success), after=0.5 (1 success/1 error)
        # delta=-0.5, threshold=-0.3 なら negative_transfer=True
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "session_id": "s3", "outcome": "error"},
        ]
        results = compute_negative_transfer(usage_data, delta_threshold=-0.3)
        assert len(results) == 1
        r = results[0]
        assert r["negative_transfer"] is True

    def test_result_fields_are_present(self):
        """返り値の各要素に必須フィールドが存在すること。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "error"},
        ]
        results = compute_negative_transfer(usage_data)
        assert len(results) == 1
        r = results[0]
        assert "skill_name" in r
        assert "delta_score" in r
        assert "negative_transfer" in r
        assert "before_score" in r
        assert "after_score" in r


class TestComputeNegativeTransferEdgeCases:
    """エッジケースのテスト。"""

    def test_empty_usage_data_returns_empty_list(self):
        """usage_data が空のとき [] を返すこと。"""
        results = compute_negative_transfer([])
        assert results == []

    def test_single_skill_only_returns_empty_list(self):
        """スキルが1種類のみ（追加スキルなし）のとき [] を返すこと。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-02T00:00:00Z", "session_id": "s1", "outcome": "success"},
        ]
        results = compute_negative_transfer(usage_data)
        assert results == []

    def test_missing_before_data_is_skipped(self):
        """skill_added より前の outcome レコードがない場合 skip されること（KeyError なし）。"""
        usage_data = [
            # review が最初に登場（基準点）
            {"skill_name": "review", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            # ship が後から登場（追加スキル扱い）
            {"skill_name": "ship", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            # review の after のみある（before なし）
            {"skill_name": "review", "ts": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "success"},
        ]
        # review は before データがないため skip
        results = compute_negative_transfer(usage_data)
        # review の before は ts < ship_added_ts のレコードがない → skip
        assert isinstance(results, list)

    def test_missing_after_data_is_skipped(self):
        """追加スキルより後の outcome レコードがない場合 skip されること（KeyError なし）。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "session_id": "s1", "outcome": "success"},
            # review が追加されるが、その後 ship のレコードがない
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
        ]
        results = compute_negative_transfer(usage_data)
        assert results == []

    def test_multiple_skills_multiple_transfers(self):
        """複数スキルの転移を同時に検出できること。

        ship と review が同時刻に初回登場（既存スキル）し、
        その後 implement が追加された場合に両スキルの転移を検出する。
        """
        usage_data = [
            # ship と review が同時刻に初回登場（既存スキル、同じ基準点）
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-01T02:00:00Z", "session_id": "s1", "outcome": "error"},
            # implement が後から追加
            {"skill_name": "implement", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            # ship: after は error（転移あり）
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "error"},
            {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "session_id": "s3", "outcome": "error"},
            # review: after は success（転移なし）
            {"skill_name": "review", "ts": "2026-01-03T02:00:00Z", "session_id": "s3", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-03T03:00:00Z", "session_id": "s3", "outcome": "success"},
        ]
        results = compute_negative_transfer(usage_data)
        result_map = {r["skill_name"]: r for r in results}
        assert "ship" in result_map
        assert result_map["ship"]["negative_transfer"] is True
        assert "review" in result_map
        assert result_map["review"]["negative_transfer"] is False

    def test_records_without_required_fields_no_error(self):
        """不完全なレコードが含まれても KeyError が発生しないこと。"""
        usage_data = [
            {"skill_name": "ship"},  # ts なし、outcome なし
            {},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z"},  # outcome なし
        ]
        # エラーなく処理されること
        results = compute_negative_transfer(usage_data)
        assert isinstance(results, list)

    def test_timestamp_field_fallback(self):
        """ts フィールドがない場合に timestamp フィールドにフォールバックすること。"""
        usage_data = [
            # timestamp フィールドを使用（ts なし）
            {"skill_name": "ship", "timestamp": "2026-01-01T00:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "ship", "timestamp": "2026-01-01T01:00:00Z", "session_id": "s1", "outcome": "success"},
            {"skill_name": "review", "timestamp": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
            {"skill_name": "ship", "timestamp": "2026-01-03T00:00:00Z", "session_id": "s3", "outcome": "error"},
        ]
        results = compute_negative_transfer(usage_data)
        assert len(results) == 1
        assert results[0]["skill_name"] == "ship"

    def test_window_parameter_limits_records(self):
        """window パラメータで before/after のレコード数を制限できること。"""
        usage_data = [
            # before: 5件 success
            {"skill_name": "ship", "ts": f"2026-01-01T0{i}:00:00Z", "session_id": "s1", "outcome": "success"}
            for i in range(5)
        ] + [
            # 新規スキル追加
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "session_id": "s2", "outcome": "success"},
        ] + [
            # after: 3件 error
            {"skill_name": "ship", "ts": f"2026-01-03T0{i}:00:00Z", "session_id": "s3", "outcome": "error"}
            for i in range(3)
        ]
        results = compute_negative_transfer(usage_data, window=3)
        assert len(results) == 1
        r = results[0]
        # window=3: before の最後3件は全 success → before_score=1.0
        # after の最初3件は全 error → after_score=0.0
        assert r["before_score"] == pytest.approx(1.0)
        assert r["after_score"] == pytest.approx(0.0)
        assert r["negative_transfer"] is True
