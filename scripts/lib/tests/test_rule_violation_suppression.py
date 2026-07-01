#!/usr/bin/env python3
"""#103: 情報レーン advisory（rule_violation_observed / proposable_global）の dismiss 配線テスト。

rule_violation_observed は issue 形（type/file/detail）を持たないため、そのまま
suppression_ledger.dedup_key に渡すと全項目が同一キーへ collapse する。violated_command 単位の
安定 identity へ変換して dismiss（record_rejection）→ 次回 surface から畳めることを検証する。
"""
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib_dir))

import remediation.suppression_ledger as sl  # noqa: E402
from rule_violation_lane import rule_violation_suppression_issue  # noqa: E402


class TestRuleViolationSuppressionIssue:
    def test_identity_keyed_by_violated_command(self):
        a = rule_violation_suppression_issue({"violated_command": "cd", "count": 142})
        b = rule_violation_suppression_issue({"violated_command": "cd", "count": 5})
        assert sl.dedup_key(a) == sl.dedup_key(b), "同じ違反コマンドは同一 dismiss で抑制されるべき"

    def test_distinct_commands_distinct_keys(self):
        a = rule_violation_suppression_issue({"violated_command": "cd"})
        b = rule_violation_suppression_issue({"violated_command": "pkill"})
        assert sl.dedup_key(a) != sl.dedup_key(b)

    def test_falls_back_to_pattern_head(self):
        """violated_command 欠落時は pattern の先頭語を head にする。"""
        issue = rule_violation_suppression_issue({"pattern": "cd /foo/bar && ls"})
        assert issue["detail"]["target"] == "cd"

    def test_type_is_rule_violation_observed(self):
        issue = rule_violation_suppression_issue({"violated_command": "cd"})
        assert issue["type"] == "rule_violation_observed"


class TestRuleViolationDismissRoundTrip:
    def test_dismiss_then_suppressed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        v = {"violated_command": "cd", "count": 142}
        issue = rule_violation_suppression_issue(v)
        assert sl.is_suppressed(issue, slug="proj") is False
        sl.record_rejection(issue, slug="proj")
        assert sl.is_suppressed(issue, slug="proj") is True

    def test_dismiss_one_command_leaves_others(self, tmp_path, monkeypatch):
        """cd を dismiss しても pkill は surface される（violated_command 単位の抑制）。"""
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        cd = {"violated_command": "cd", "count": 142}
        pkill = {"violated_command": "pkill", "count": 30}
        sl.record_rejection(rule_violation_suppression_issue(cd), slug="proj")

        # phases_remediate と同じ surface フィルタ（violated_command 単位）を再現。
        surface = [
            v for v in [cd, pkill]
            if not sl.is_suppressed(rule_violation_suppression_issue(v), slug="proj")
        ]
        heads = [v["violated_command"] for v in surface]
        assert heads == ["pkill"], f"cd は畳まれ pkill のみ surface されるべき: {heads}"

    def test_ttl_resurfaces(self, tmp_path, monkeypatch):
        """TTL を過ぎた dismiss は再 surface される（環境変化の再評価機会）。"""
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        issue = rule_violation_suppression_issue({"violated_command": "cd"})
        sl.record_rejection(issue, slug="proj", now=0.0, ttl_days=45)
        # TTL 内: 抑制中
        assert sl.is_suppressed(issue, slug="proj", now=1.0) is True
        # TTL 超過: 再 surface
        future = 46 * sl.DAY_SECONDS
        assert sl.is_suppressed(issue, slug="proj", now=future) is False


class TestProposableGlobalDismiss:
    """proposable_global は issue dict そのままで dismiss 可能（native dedup_key）。"""

    def test_dismiss_global_issue(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        issue = {"type": "line_limit_violation", "file": "safety.md",
                 "detail": {"lines": 11, "limit": 10}}
        parts = sl.filter_suppressed([issue], slug="proj")
        assert parts["surface"] == [issue] and parts["suppressed"] == []
        sl.record_rejection(issue, slug="proj")
        parts2 = sl.filter_suppressed([issue], slug="proj")
        assert parts2["surface"] == [] and parts2["suppressed"] == [issue]
