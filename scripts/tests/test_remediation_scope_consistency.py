#!/usr/bin/env python3
"""#477-1: remediation の scope 分類不整合の解消テスト。

`impact_scope: "global"`（~/.claude/rules/ 配下のグローバル rule）の proposable item が
`proposable_custom_individual` に振り分けられ、同時に集計が `proposable_global: 0` になる
バグの再発防止。SKILL.md 上 global scope は「参考値・対応不要」であり、個別承認の
AskUserQuestion でユーザーに提示してはならない。

`classify_artifact_origin`（パス由来）と `compute_impact_scope`（impact 由来）の判定が
食い違っても、partition は impact_scope を最終権威として global へ寄せる
（決定論・LLM 非依存）。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from remediation import partition_proposable_by_scope  # noqa: E402


def _issue(scope, origin_hint=None, conf=0.95, file_="x.md"):
    d = {
        "type": "line_limit_violation",
        "file": file_,
        "confidence_score": conf,
        "impact_scope": scope,
        "detail": {"lines": 11, "limit": 10},
    }
    if origin_hint is not None:
        d["_origin_hint"] = origin_hint
    return d


class TestPartitionByScope:
    def test_global_impact_scope_goes_to_global(self):
        """impact_scope == "global" は origin が custom でも proposable_global へ。"""
        items = [_issue("global", origin_hint="custom")]
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "custom")
        assert len(out["global"]) == 1
        assert out["custom"] == []

    def test_file_scope_with_custom_origin_goes_custom(self):
        items = [_issue("file")]
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "custom")
        assert len(out["custom"]) == 1
        assert out["global"] == []

    def test_origin_global_goes_global_even_if_scope_file(self):
        """origin == "global"（~/.claude/skills/ 等）も global 側へ寄せる。"""
        items = [_issue("file")]
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "global")
        assert len(out["global"]) == 1
        assert out["custom"] == []

    def test_count_matches_lists(self):
        items = [
            _issue("global", file_="a.md"),
            _issue("file", file_="b.md"),
            _issue("project", file_="c.md"),
        ]
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "custom")
        assert len(out["global"]) == 1
        assert len(out["custom"]) == 2

    def test_no_global_landing_in_individual_after_partition(self):
        """回帰: global item が custom 経由で individual に流れないことを保証する。

        partition_proposable_by_scope の custom リストには impact_scope == global が
        1件も含まれてはならない。
        """
        items = [_issue("global"), _issue("file"), _issue("global")]
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "custom")
        assert all(it.get("impact_scope") != "global" for it in out["custom"])
        assert len(out["global"]) == 2

    def test_does_not_mutate_input(self):
        items = [_issue("global"), _issue("file")]
        before = [dict(i) for i in items]
        partition_proposable_by_scope(items, origin_resolver=lambda p: "custom")
        assert items == before

    def test_empty(self):
        out = partition_proposable_by_scope([], origin_resolver=lambda p: "custom")
        assert out == {"custom": [], "global": []}


class TestHookInstallBypassesScopeFolding:
    """#225: hook インストール系アクション（type が `*_hook_candidate`）は impact_scope が
    global でも scope-based な折り畳み（proposable_global の1行サマリ）を経由させず、
    常に custom 側（個別承認レーン）へ合流させる。

    hook install は ~/.claude 配下の共有設定を書き換える＝影響半径が最大の一方、
    scope-based 折り畳みが「参考値・対応不要」として1行に潰してしまうと、生成された
    スクリプト/diff の中身をユーザーが見ないまま承認されない/放置される逆転が起きる。
    折り畳みは低リスク型（行数超過 advisory 等）専用に限定する。
    """

    def test_hook_candidate_global_scope_goes_to_custom(self):
        """tool_usage_hook_candidate は impact_scope == "global" でも custom へ合流する。"""
        items = [_issue("global", conf=0.75, file_="/home/x/.claude/hooks/check.py")]
        items[0]["type"] = "tool_usage_hook_candidate"
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "global")
        assert len(out["custom"]) == 1
        assert out["global"] == []

    def test_low_risk_type_still_folds_to_global(self):
        """line_limit_violation 等の低リスク型は従来どおり global へ折り畳まれる（回帰防止）。"""
        items = [_issue("global", conf=0.75)]  # type == "line_limit_violation"（_issue の既定）
        out = partition_proposable_by_scope(items, origin_resolver=lambda p: "global")
        assert out["custom"] == []
        assert len(out["global"]) == 1

    def test_mixed_hook_and_low_risk_partition_independently(self):
        hook_issue = _issue("global", conf=0.75, file_="/home/x/.claude/hooks/check.py")
        hook_issue["type"] = "tool_usage_hook_candidate"
        low_risk_issue = _issue("global", conf=0.75, file_="/home/x/.claude/rules/foo.md")
        out = partition_proposable_by_scope(
            [hook_issue, low_risk_issue], origin_resolver=lambda p: "global"
        )
        assert out["custom"] == [hook_issue]
        assert out["global"] == [low_risk_issue]

    def test_does_not_mutate_input(self):
        hook_issue = _issue("global", conf=0.75, file_="/home/x/.claude/hooks/check.py")
        hook_issue["type"] = "tool_usage_hook_candidate"
        before = dict(hook_issue)
        partition_proposable_by_scope([hook_issue], origin_resolver=lambda p: "global")
        assert hook_issue == before
