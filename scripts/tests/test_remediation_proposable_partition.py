#!/usr/bin/env python3
"""#377-3: proposable を confidence しきい値で「個別承認」「まとめてスキップ」に分割する。

Step 5.5 の per-item 承認 MUST が低 confidence FP 群（conf 0.5 中心）で「質問攻め」に
なる問題を、決定論分割で塞ぐ。SKILL.md の文言だけに頼らず、しきい値判定をコードに
落とす（MUST が効かない class の再発防止 = #375-#377 シリーズの drift 防止思想）。

partition_proposable_by_confidence の境界・ソート・既定しきい値を検証する。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from remediation import (  # noqa: E402
    partition_proposable_by_confidence,
    PROPOSABLE_INDIVIDUAL_CONFIDENCE,
)


def _issue(conf, type_="hardcoded_value", file_="x.md"):
    return {"type": type_, "file": file_, "confidence_score": conf, "detail": {}}


class TestPartitionProposable:
    def test_default_threshold_is_0_7(self):
        assert PROPOSABLE_INDIVIDUAL_CONFIDENCE == 0.7

    def test_high_confidence_goes_individual(self):
        items = [_issue(0.85), _issue(0.90)]
        out = partition_proposable_by_confidence(items)
        assert len(out["individual"]) == 2
        assert out["batch_skip"] == []

    def test_low_confidence_goes_batch_skip(self):
        items = [_issue(0.5), _issue(0.6), _issue(0.65)]
        out = partition_proposable_by_confidence(items)
        assert out["individual"] == []
        assert len(out["batch_skip"]) == 3

    def test_boundary_value_is_individual(self):
        # confidence == threshold は individual 側（>= 判定）
        out = partition_proposable_by_confidence([_issue(0.7)])
        assert len(out["individual"]) == 1
        assert out["batch_skip"] == []

    def test_just_below_boundary_is_batch_skip(self):
        out = partition_proposable_by_confidence([_issue(0.69)])
        assert out["individual"] == []
        assert len(out["batch_skip"]) == 1

    def test_mixed_split(self):
        items = [_issue(0.5), _issue(0.85), _issue(0.6), _issue(0.7), _issue(0.5)]
        out = partition_proposable_by_confidence(items)
        assert len(out["individual"]) == 2  # 0.85, 0.7
        assert len(out["batch_skip"]) == 3  # 0.6, 0.5, 0.5

    def test_individual_sorted_by_confidence_desc(self):
        items = [_issue(0.7), _issue(0.9), _issue(0.85)]
        out = partition_proposable_by_confidence(items)
        confs = [i["confidence_score"] for i in out["individual"]]
        assert confs == [0.9, 0.85, 0.7]

    def test_batch_skip_sorted_by_confidence_desc(self):
        items = [_issue(0.5), _issue(0.65), _issue(0.6)]
        out = partition_proposable_by_confidence(items)
        confs = [i["confidence_score"] for i in out["batch_skip"]]
        assert confs == [0.65, 0.6, 0.5]

    def test_empty_list(self):
        out = partition_proposable_by_confidence([])
        assert out == {"individual": [], "batch_skip": []}

    def test_missing_confidence_treated_as_low(self):
        # confidence_score 欠落は質問攻め回避側（batch_skip）に倒す
        out = partition_proposable_by_confidence([{"type": "x", "file": "x.md"}])
        assert out["individual"] == []
        assert len(out["batch_skip"]) == 1

    def test_explicit_threshold_override(self):
        items = [_issue(0.6), _issue(0.65)]
        out = partition_proposable_by_confidence(items, threshold=0.6)
        assert len(out["individual"]) == 2  # both >= 0.6
        out2 = partition_proposable_by_confidence(items, threshold=0.8)
        assert out2["individual"] == []  # none >= 0.8

    def test_does_not_mutate_input(self):
        items = [_issue(0.5), _issue(0.85)]
        before = [dict(i) for i in items]
        partition_proposable_by_confidence(items)
        assert items == before
