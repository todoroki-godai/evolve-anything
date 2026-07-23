"""issue_schema の is_hook_install_issue_type 判定テスト（#225）。

remediation.partition_proposable_by_scope が hook インストール系アクションを
scope-based な折り畳みからバイパスするための単一ソース predicate。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from issue_schema import TOOL_USAGE_HOOK_CANDIDATE, is_hook_install_issue_type


def test_tool_usage_hook_candidate_matches():
    assert is_hook_install_issue_type(TOOL_USAGE_HOOK_CANDIDATE) is True


def test_future_prefixed_hook_candidate_type_matches():
    """`*_hook_candidate` の glob 意図: 別 prefix の将来型も拾う。"""
    assert is_hook_install_issue_type("rule_violation_hook_candidate") is True


def test_unrelated_type_does_not_match():
    assert is_hook_install_issue_type("line_limit_violation") is False
    assert is_hook_install_issue_type("tool_usage_rule_candidate") is False


def test_empty_or_none_is_false():
    assert is_hook_install_issue_type("") is False
