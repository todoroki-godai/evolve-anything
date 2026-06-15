#!/usr/bin/env python3
"""#522-1: skill_triage CREATE の confidence が default 0.5 に降格し承認レーンに
乗らない問題の回帰テスト。

skill_triage の CREATE 候補は confidence 0.70（>= PROPOSABLE_INDIVIDUAL_CONFIDENCE）
だが、remediation issue 化で top-level confidence_score が default 0.5 に降格していた。
これにより partition_proposable_by_confidence で常に batch_skip 落ちし、
proposable_custom_individual（個別承認レーン）に乗らなかった。

compute_confidence_score が skill_triage 系 issue の detail["confidence"] を
top-level に引き継ぐこと、結果として CREATE が individual レーンに乗ることを検証する。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from remediation import (  # noqa: E402
    compute_confidence_score,
    classify_issue,
    partition_proposable_by_confidence,
    PROPOSABLE_INDIVIDUAL_CONFIDENCE,
)
from issue_schema import (  # noqa: E402
    SKILL_TRIAGE_CREATE,
    SKILL_TRIAGE_UPDATE,
    SKILL_TRIAGE_SPLIT,
    SKILL_TRIAGE_MERGE,
    make_skill_triage_issue,
)


def _triage_issue(action, confidence):
    return make_skill_triage_issue(
        {
            "action": action,
            "skill": "foo-skill",
            "confidence": confidence,
            "evidence": {},
            "suggestion": "create foo-skill",
        }
    )


class TestSkillTriageConfidence:
    def test_create_confidence_preserved_from_detail(self):
        # CREATE confidence 0.70 が default 0.5 に降格せず detail から引き継がれる
        issue = _triage_issue("CREATE", 0.70)
        assert compute_confidence_score(issue) == 0.70

    def test_update_confidence_preserved(self):
        issue = _triage_issue("UPDATE", 0.75)
        assert compute_confidence_score(issue) == 0.75

    def test_split_confidence_preserved(self):
        issue = _triage_issue("SPLIT", 0.72)
        assert compute_confidence_score(issue) == 0.72

    def test_merge_confidence_preserved(self):
        issue = _triage_issue("MERGE", 0.71)
        assert compute_confidence_score(issue) == 0.71

    def test_missing_detail_confidence_falls_back_to_default(self):
        # detail に confidence が無い場合は default 0.5
        issue = {"type": SKILL_TRIAGE_CREATE, "file": "x.md", "detail": {}}
        assert compute_confidence_score(issue) == 0.5

    def test_create_lands_in_individual_lane(self):
        # 全体経路: classify_issue → partition で CREATE が individual に乗る
        issue = _triage_issue("CREATE", 0.70)
        classified = classify_issue(issue)
        out = partition_proposable_by_confidence(
            [classified], threshold=PROPOSABLE_INDIVIDUAL_CONFIDENCE
        )
        assert len(out["individual"]) == 1
        assert out["batch_skip"] == []
