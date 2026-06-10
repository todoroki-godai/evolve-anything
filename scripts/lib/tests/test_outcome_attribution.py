"""per-skill outcome 帰属 + evolve ターゲットランキング配線のテスト（#433 先行スコープ）。

corrections 非依存の2軸（一発成功率 / rework 率）だけをスキル単位に分解し、
skill_triage の候補順位に自動入力する。LLM 非依存・決定論。

データ契約（実測で確認、capture_rate.py の join パターンに準拠）:
  - usage レコード: skill / skill_name → session_id（1 skill 呼び出し = 1 行）
  - sessions レコード: session_id → error_count / tool_sequence

帰属は **in-memory のリストのみ** を入力にする（store 再読込なし = dry-run 安全）。
monkeypatch は文字列ターゲットを避け、import した module を直接参照する（既知 pitfall 準拠）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import outcome_attribution as oa  # noqa: E402


def _usage(skill: str, session_id: str, *, field: str = "skill_name") -> dict:
    return {field: skill, "session_id": session_id}


def _session(sid: str, *, error_count: int = 0, tool_sequence=None) -> dict:
    return {
        "session_id": sid,
        "error_count": error_count,
        "tool_sequence": tool_sequence if tool_sequence is not None else [],
    }


# ---------- per-skill 帰属 ----------

class TestAttributeOutcomes:
    def test_empty_inputs_degraded(self):
        attr = oa.attribute_outcomes(usage=[], sessions=[])
        assert attr == {}

    def test_skill_with_no_sessions_is_degraded(self):
        # usage が skill→session を指すが、その session が sessions に無い
        usage = [_usage("foo", "missing-sid")]
        sessions: list = []
        attr = oa.attribute_outcomes(usage=usage, sessions=sessions)
        assert "foo" in attr
        rec = attr["foo"]
        assert rec["degraded"] is True
        assert rec["n_sessions"] == 0
        assert rec["first_try_success"] is None
        assert rec["rework"] is None

    def test_first_try_success_attributed_per_skill(self):
        # foo: 2 sessions, 1 clean (error_count==0), 1 dirty → first_try = 0.5
        usage = [_usage("foo", "s1"), _usage("foo", "s2")]
        sessions = [
            _session("s1", error_count=0),
            _session("s2", error_count=3),
        ]
        attr = oa.attribute_outcomes(usage=usage, sessions=sessions)
        assert attr["foo"]["first_try_success"] == pytest.approx(0.5)
        assert attr["foo"]["n_sessions"] == 2
        assert attr["foo"]["degraded"] is False

    def test_rework_attributed_per_skill(self):
        # bar の session に検証なし連続 Edit が 3 以上 → rework
        usage = [_usage("bar", "s1"), _usage("bar", "s2")]
        sessions = [
            _session("s1", tool_sequence=["Edit", "Edit", "Edit"]),  # burst
            _session("s2", tool_sequence=["Edit", "Bash", "Edit"]),  # 介在で reset
        ]
        attr = oa.attribute_outcomes(usage=usage, sessions=sessions)
        # 2 edit sessions, 1 rework → 0.5
        assert attr["bar"]["rework"] == pytest.approx(0.5)

    def test_session_with_no_error_count_excluded_from_first_try(self):
        # error_count None のセッションは first_try 分母から除外（dict.get None pitfall 回避）
        usage = [_usage("foo", "s1"), _usage("foo", "s2")]
        sessions = [
            {"session_id": "s1", "tool_sequence": []},  # error_count 欠損
            _session("s2", error_count=0),
        ]
        attr = oa.attribute_outcomes(usage=usage, sessions=sessions)
        # 有効な error_count は s2 のみ → 1/1 = 1.0
        assert attr["foo"]["first_try_success"] == pytest.approx(1.0)

    def test_skill_field_fallback(self):
        # skill フィールド（skill_name でなく）も拾う
        usage = [_usage("foo", "s1", field="skill")]
        sessions = [_session("s1", error_count=0)]
        attr = oa.attribute_outcomes(usage=usage, sessions=sessions)
        assert "foo" in attr
        assert attr["foo"]["first_try_success"] == pytest.approx(1.0)

    def test_dedup_session_per_skill(self):
        # 同 skill が同 session を複数回呼んでも session は 1 回だけ数える
        usage = [_usage("foo", "s1"), _usage("foo", "s1"), _usage("foo", "s1")]
        sessions = [_session("s1", error_count=5)]
        attr = oa.attribute_outcomes(usage=usage, sessions=sessions)
        assert attr["foo"]["n_sessions"] == 1
        assert attr["foo"]["first_try_success"] == pytest.approx(0.0)


# ---------- outcome priority スコア ----------

class TestOutcomePriority:
    def test_degraded_skill_neutral_priority(self):
        # データ欠損は順位を動かさない（neutral=0.0）
        rec = {"first_try_success": None, "rework": None, "degraded": True, "n_sessions": 0}
        assert oa.outcome_priority(rec) == pytest.approx(0.0)

    def test_bad_outcomes_high_priority(self):
        # 一発成功率 0 + rework 1.0 → 最大優先度（=最も悪い = 進化対象として上げる）
        rec = {"first_try_success": 0.0, "rework": 1.0, "degraded": False, "n_sessions": 5}
        assert oa.outcome_priority(rec) == pytest.approx(1.0)

    def test_good_outcomes_low_priority(self):
        rec = {"first_try_success": 1.0, "rework": 0.0, "degraded": False, "n_sessions": 5}
        assert oa.outcome_priority(rec) == pytest.approx(0.0)

    def test_partial_axis_missing_uses_available(self):
        # rework のみ欠損 → first_try だけで算出（None ソート落ち回避）
        rec = {"first_try_success": 0.0, "rework": None, "degraded": False, "n_sessions": 3}
        # (1 - 0.0) のみ → 1.0
        assert oa.outcome_priority(rec) == pytest.approx(1.0)


# ---------- ランキング配線 ----------

class TestApplyOutcomeRanking:
    def _triage(self):
        return {
            "CREATE": [],
            "UPDATE": [
                {"action": "UPDATE", "skill": "good", "confidence": 0.7},
                {"action": "UPDATE", "skill": "bad", "confidence": 0.7},
            ],
            "SPLIT": [],
            "MERGE": [],
            "OK": [],
            "skipped": False,
        }

    def _usage_sessions(self):
        # good: clean / no rework。bad: dirty + rework burst
        usage = [
            _usage("good", "g1"),
            _usage("bad", "b1"),
        ]
        sessions = [
            _session("g1", error_count=0, tool_sequence=["Edit", "Bash"]),
            _session("b1", error_count=4, tool_sequence=["Edit", "Edit", "Edit"]),
        ]
        return usage, sessions

    def test_reorders_by_outcome_priority(self):
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        result = oa.apply_outcome_ranking(triage, usage=usage, sessions=sessions)
        order = [c["skill"] for c in result["UPDATE"]]
        # bad（悪いアウトカム）が先頭に来る
        assert order == ["bad", "good"]

    def test_attaches_outcome_evidence_to_candidate(self):
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        result = oa.apply_outcome_ranking(triage, usage=usage, sessions=sessions)
        bad = next(c for c in result["UPDATE"] if c["skill"] == "bad")
        assert "outcome" in bad
        assert bad["outcome"]["priority"] > 0
        assert bad["outcome"]["first_try_success"] == pytest.approx(0.0)

    def test_ranking_evidence_records_before_after(self):
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        result = oa.apply_outcome_ranking(triage, usage=usage, sessions=sessions)
        ev = result.get("outcome_ranking")
        assert ev is not None
        # before/after の順位差分を提示できる（observability: 数字に意味を添える）
        assert ev["UPDATE"]["before"] == ["good", "bad"]
        assert ev["UPDATE"]["after"] == ["bad", "good"]
        assert ev["UPDATE"]["changed"] is True

    def test_degraded_skill_keeps_confidence_order(self):
        # アウトカムが全 degraded のとき confidence 降順（安定）を保ち順位を壊さない
        triage = {
            "CREATE": [],
            "UPDATE": [
                {"action": "UPDATE", "skill": "a", "confidence": 0.9},
                {"action": "UPDATE", "skill": "b", "confidence": 0.6},
            ],
            "SPLIT": [], "MERGE": [], "OK": [], "skipped": False,
        }
        result = oa.apply_outcome_ranking(triage, usage=[], sessions=[])
        assert [c["skill"] for c in result["UPDATE"]] == ["a", "b"]
        assert result["outcome_ranking"]["UPDATE"]["changed"] is False

    def test_skipped_triage_passthrough(self):
        triage = {"skipped": True, "reason": "no_skills_found"}
        result = oa.apply_outcome_ranking(triage, usage=[], sessions=[])
        assert result["skipped"] is True
        assert "outcome_ranking" not in result

    def test_does_not_mutate_input(self):
        triage = self._triage()
        usage, sessions = self._usage_sessions()
        before_ids = [id(c) for c in triage["UPDATE"]]
        oa.apply_outcome_ranking(triage, usage=usage, sessions=sessions)
        # 入力 triage の UPDATE 並びは変わらない（純粋関数）
        assert [id(c) for c in triage["UPDATE"]] == before_ids
        assert "outcome" not in triage["UPDATE"][0]
