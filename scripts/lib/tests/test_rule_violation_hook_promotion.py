#!/usr/bin/env python3
"""#585: 高頻度 rule_violation_observed を hook_candidate へ昇格するテスト。

builtin_replaceable は tool_usage_hook_candidate に昇格して remediation proposable に
乗るのに、rule_violation_observed（rule_installed_but_not_enforced）は surface のみで
hook 候補にも remediation proposable にも乗らなかった。最も enforce すべき高頻度違反
（例: cd を 626 回）を tool_usage_hook_candidate 相当の issue に昇格させる配線を検証する。

決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib))

from rule_violation_lane import (  # noqa: E402
    RULE_VIOLATION_HOOK_THRESHOLD,
    make_hook_candidate_issues_from_rule_violations,
)
from issue_schema import (  # noqa: E402
    TOOL_USAGE_HOOK_CANDIDATE,
    HOOK_SCRIPT_PATH,
    HOOK_SCRIPT_CONTENT,
    HOOK_TARGET_COMMANDS,
    HOOK_TOTAL_COUNT,
)


class TestMakeHookCandidateIssuesFromRuleViolations:
    def test_high_frequency_violation_promoted_to_hook_candidate(self):
        violations = [
            {
                "pattern": "cd somewhere",
                "count": 626,
                "violated_command": "cd",
                "reason": "rule_installed_but_not_enforced",
            },
        ]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == TOOL_USAGE_HOOK_CANDIDATE
        detail = issue["detail"]
        assert "cd" in detail[HOOK_TARGET_COMMANDS]
        assert detail[HOOK_TOTAL_COUNT] == 626
        # 実際に書き込める hook scaffold が乗っていること（remediation が消費する）
        assert detail[HOOK_SCRIPT_PATH]
        assert detail[HOOK_SCRIPT_CONTENT]
        # enforcement hook は違反コマンドを block する内容を含む
        assert "cd" in detail[HOOK_SCRIPT_CONTENT]

    def test_low_frequency_violation_not_promoted(self):
        violations = [
            {"pattern": "cd somewhere", "count": 3, "violated_command": "cd"},
        ]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        assert issues == []

    def test_threshold_boundary_inclusive(self):
        violations = [
            {
                "pattern": "cd x",
                "count": RULE_VIOLATION_HOOK_THRESHOLD,
                "violated_command": "cd",
            },
        ]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        assert len(issues) == 1

    def test_multiple_violations_grouped_into_single_hook(self):
        violations = [
            {"pattern": "cd a", "count": 626, "violated_command": "cd"},
            {"pattern": "pkill -f x", "count": 412, "violated_command": "pkill"},
        ]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        # 全違反を 1 つの enforcement hook scaffold にまとめる（settings.json 1 つ）
        assert len(issues) == 1
        detail = issues[0]["detail"]
        assert set(detail[HOOK_TARGET_COMMANDS]) == {"cd", "pkill"}
        # total_count は全違反の合算
        assert detail[HOOK_TOTAL_COUNT] == 626 + 412

    def test_missing_violated_command_skipped(self):
        violations = [{"pattern": "?? weird", "count": 999}]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        assert issues == []

    def test_empty_input(self):
        assert make_hook_candidate_issues_from_rule_violations([]) == []

    def test_source_marks_rule_violation_lane(self):
        violations = [{"pattern": "cd a", "count": 626, "violated_command": "cd"}]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        assert issues[0]["source"] == "rule_violation_observed"


class TestEnforcementHookScriptMultiwordMatching:
    """#222: 生成された enforcement hook 自体が複数語 prohibited を
    先頭語への縮約でなくトークン列 prefix 一致で判定すること。"""

    @staticmethod
    def _load_check_command(script_content):
        namespace: dict = {}
        exec(compile(script_content, "<generated-hook>", "exec"), namespace)  # noqa: S102
        return namespace["check_command"]

    def test_multiword_prohibited_matches_prefix_only(self):
        violations = [
            {
                "pattern": "git checkout -b x",
                "count": 30,
                "violated_command": "git checkout -b",
            },
        ]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        check_command = self._load_check_command(issues[0]["detail"][HOOK_SCRIPT_CONTENT])
        assert check_command("git status") is None
        assert check_command("git checkout -b feature/x") is not None

    def test_single_word_prohibited_still_blocks_all_invocations(self):
        violations = [
            {"pattern": "cd a", "count": 626, "violated_command": "cd"},
        ]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        check_command = self._load_check_command(issues[0]["detail"][HOOK_SCRIPT_CONTENT])
        assert check_command("cd foo") is not None
        assert check_command("git status") is None


class TestPromotedIssueLandsInRemediationProposable:
    """昇格した issue が remediation 分類で proposable に乗ることを確認する（#585 の眼目）。

    builtin_replaceable の hook_candidate と同じ type を使うため、confidence 0.75 で
    proposable 帯（>= 0.5）かつ個別承認帯（>= 0.7）に分類される。
    """

    def test_promoted_violation_classified_proposable(self):
        from remediation import classify_issues

        violations = [{"pattern": "cd a", "count": 626, "violated_command": "cd"}]
        issues = make_hook_candidate_issues_from_rule_violations(violations)
        classified = classify_issues(issues)
        proposable_types = [i["type"] for i in classified["proposable"]]
        assert TOOL_USAGE_HOOK_CANDIDATE in proposable_types
