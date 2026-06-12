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
