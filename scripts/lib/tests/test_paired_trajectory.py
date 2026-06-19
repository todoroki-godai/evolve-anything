"""compute_paired_trajectory() のユニットテスト（#15, paired trajectory auditing 観測版）。

SkillAudit (arXiv 2606.14239) の「同一タスクをスキル有/無で実行し挙動を対照する」を、
能動再実行せず既存テレメトリ（usage + sessions）からの**準実験的観測**として実装した関数の
回帰ガード。

compute_component_transfer（時系列前後デルタ）とは別物であることを明示的に検証する:
本関数は同一 task-type バケット内で skill 有/無のセッションを対照し、挙動メトリクス
（一発成功率 = error_count==0 セッション割合）のデルタを算出する。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.usage import compute_paired_trajectory


def _usage(skill, sid):
    return {"skill_name": skill, "session_id": sid}


def _sess(sid, error_count, seq=None):
    return {"session_id": sid, "error_count": error_count, "tool_sequence": seq or []}


class TestPairedTrajectoryBasic:
    def test_empty_inputs(self):
        assert compute_paired_trajectory(usage=[], sessions=[]) == []

    def test_no_pairing_when_skill_always_present(self):
        """対象スキルが全 task-type で常に存在 → without 腕が無く paired 不成立 → []。"""
        # task-type = "ship" を含むセッション群。target=review は常に同居。
        usage = [
            _usage("ship", "s1"), _usage("review", "s1"),
            _usage("ship", "s2"), _usage("review", "s2"),
        ]
        sessions = [_sess("s1", 0), _sess("s2", 1)]
        assert compute_paired_trajectory(usage=usage, sessions=sessions) == []

    def test_paired_delta_positive(self):
        """同一 task-type（ship を含む）で review 有のほうが一発成功率が高い → positive delta。"""
        # task-type key = ship を含む context。
        # with-review: s1(clean), s2(clean) → success 1.0
        # without-review: s3(error), s4(error) → success 0.0
        usage = [
            _usage("ship", "s1"), _usage("review", "s1"),
            _usage("ship", "s2"), _usage("review", "s2"),
            _usage("ship", "s3"),
            _usage("ship", "s4"),
        ]
        sessions = [
            _sess("s1", 0), _sess("s2", 0),
            _sess("s3", 1), _sess("s4", 2),
        ]
        results = compute_paired_trajectory(usage=usage, sessions=sessions)
        rec = {r["skill"]: r for r in results}
        assert "review" in rec
        r = rec["review"]
        assert r["with_success"] == pytest.approx(1.0)
        assert r["without_success"] == pytest.approx(0.0)
        assert r["behavior_delta"] == pytest.approx(1.0)
        assert r["paired_task_types"] == 1
        assert r["n_with"] == 2
        assert r["n_without"] == 2

    def test_paired_delta_negative(self):
        """skill 有のほうが一発成功率が低い → negative delta（スキルが挙動を悪化させた兆候）。"""
        usage = [
            _usage("ship", "s1"), _usage("flaky", "s1"),
            _usage("ship", "s2"), _usage("flaky", "s2"),
            _usage("ship", "s3"),
            _usage("ship", "s4"),
        ]
        sessions = [
            _sess("s1", 2), _sess("s2", 1),   # with-flaky: success 0.0
            _sess("s3", 0), _sess("s4", 0),   # without: success 1.0
        ]
        results = compute_paired_trajectory(usage=usage, sessions=sessions)
        rec = {r["skill"]: r for r in results}
        assert rec["flaky"]["behavior_delta"] == pytest.approx(-1.0)
        assert rec["flaky"]["regression"] is True

    def test_result_fields_present(self):
        usage = [
            _usage("ship", "s1"), _usage("review", "s1"),
            _usage("ship", "s2"),
        ]
        sessions = [_sess("s1", 0), _sess("s2", 1)]
        results = compute_paired_trajectory(usage=usage, sessions=sessions)
        assert results, "review should be paired in the ship task-type"
        r = results[0]
        for k in (
            "skill", "behavior_delta", "with_success", "without_success",
            "n_with", "n_without", "paired_task_types", "regression",
        ):
            assert k in r


class TestPairedTrajectoryStratification:
    def test_distinct_task_types_not_mixed(self):
        """異なる task-type（ship 系 vs implement 系）を混ぜず stratify して観測する。"""
        usage = [
            # ship task-type
            _usage("ship", "s1"), _usage("review", "s1"),
            _usage("ship", "s2"),
            # implement task-type（別 task）
            _usage("implement", "s3"), _usage("review", "s3"),
            _usage("implement", "s4"),
        ]
        sessions = [
            _sess("s1", 0), _sess("s2", 1),   # ship: with-review clean, without error
            _sess("s3", 0), _sess("s4", 0),   # implement: with-review clean, without clean
        ]
        results = compute_paired_trajectory(usage=usage, sessions=sessions)
        rec = {r["skill"]: r for r in results}
        # review は 2 つの task-type でペアになる。
        assert rec["review"]["paired_task_types"] == 2

    def test_min_sessions_per_arm(self):
        """1 task-type の各腕に min_per_arm 未満しかセッションが無ければ paired 対象外。"""
        usage = [
            _usage("ship", "s1"), _usage("review", "s1"),
            _usage("ship", "s2"),
        ]
        sessions = [_sess("s1", 0), _sess("s2", 1)]
        # min_per_arm=2 を要求すると各腕 1 件なので paired 不成立。
        results = compute_paired_trajectory(
            usage=usage, sessions=sessions, min_per_arm=2
        )
        assert results == []

    def test_missing_error_count_excluded(self):
        """error_count 欠損セッションは分母から除外（None 比較落ち回避）。"""
        usage = [
            _usage("ship", "s1"), _usage("review", "s1"),
            _usage("ship", "s2"), _usage("review", "s2"),
            _usage("ship", "s3"),
        ]
        sessions = [
            _sess("s1", 0),
            {"session_id": "s2", "tool_sequence": []},  # error_count 欠損
            _sess("s3", 1),
        ]
        results = compute_paired_trajectory(usage=usage, sessions=sessions)
        rec = {r["skill"]: r for r in results}
        # with 腕は s1 のみ有効（s2 は欠損で除外）→ n_with=1。
        assert rec["review"]["n_with"] == 1
