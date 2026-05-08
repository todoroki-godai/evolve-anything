"""issues_summary のユニットテスト (#22 fleet MVP-D)。"""

import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from issues_summary import (  # noqa: E402
    IssuesSummary,
    compute_issues_summary,
)


class TestComputeIssuesSummary:
    def test_全部空ならゼロ(self):
        s = compute_issues_summary()
        assert s == IssuesSummary()
        assert s.to_dict() == {
            "line_violations": 0,
            "hardcoded_values": 0,
            "potential_duplicates": 0,
            "corrections_unprocessed": 0,
            "skill_quality_degraded_count": 0,
        }

    def test_明示的に_None_でもゼロ(self):
        s = compute_issues_summary(
            violations=None,
            hardcoded_values=None,
            duplicates=None,
            corrections=None,
            quality_baselines=None,
        )
        assert s.line_violations == 0
        assert s.hardcoded_values == 0
        assert s.potential_duplicates == 0
        assert s.corrections_unprocessed == 0
        assert s.skill_quality_degraded_count == 0

    def test_violations_と_hardcoded_と_duplicates_は単純カウント(self):
        s = compute_issues_summary(
            violations=[{"file": "a"}, {"file": "b"}, {"file": "c"}],
            hardcoded_values=[{"file": "x"}],
            duplicates=[{"name": "n1"}, {"name": "n2"}],
        )
        assert s.line_violations == 3
        assert s.hardcoded_values == 1
        assert s.potential_duplicates == 2

    def test_corrections_processed_は除外(self):
        corrections = [
            {"reflect_status": "applied", "msg": "done"},
            {"reflect_status": "pending", "msg": "wait"},
            {"msg": "no status"},
        ]
        s = compute_issues_summary(corrections=corrections)
        assert s.corrections_unprocessed == 2

    def test_corrections_全て_applied_ならゼロ(self):
        corrections = [
            {"reflect_status": "applied"},
            {"reflect_status": "applied"},
        ]
        s = compute_issues_summary(corrections=corrections)
        assert s.corrections_unprocessed == 0

    def test_skill_quality_degraded_検出(self):
        # baseline 0.9 → moving 0.6 = 33% 劣化 → degraded
        baselines = [
            {"skill_name": "drop", "score": 0.9},
            {"skill_name": "drop", "score": 0.9},
            {"skill_name": "drop", "score": 0.6},
            {"skill_name": "drop", "score": 0.6},
            # stable: 劣化なし
            {"skill_name": "stable", "score": 0.8},
            {"skill_name": "stable", "score": 0.8},
            {"skill_name": "stable", "score": 0.8},
            {"skill_name": "stable", "score": 0.8},
        ]
        s = compute_issues_summary(quality_baselines=baselines)
        assert s.skill_quality_degraded_count == 1

    def test_skill_quality_records_が1件なら判定不能(self):
        baselines = [{"skill_name": "x", "score": 0.5}]
        s = compute_issues_summary(quality_baselines=baselines)
        assert s.skill_quality_degraded_count == 0

    def test_不正なエントリは無視する(self):
        # 非 Mapping や score 欠損は静かに skip
        baselines = [
            "garbage",
            {"skill_name": "ok", "score": 0.8},
            {"skill_name": "ok"},  # score 欠損
            {"skill_name": "ok", "score": 0.8},
        ]
        s = compute_issues_summary(quality_baselines=baselines)
        # ok は score 2 件のみで baseline=0.8, avg=0.8 → degraded ではない
        assert s.skill_quality_degraded_count == 0

    def test_to_dict_は_5_キーのみ(self):
        s = IssuesSummary(line_violations=1)
        d = s.to_dict()
        assert set(d.keys()) == {
            "line_violations",
            "hardcoded_values",
            "potential_duplicates",
            "corrections_unprocessed",
            "skill_quality_degraded_count",
        }
        assert d["line_violations"] == 1
