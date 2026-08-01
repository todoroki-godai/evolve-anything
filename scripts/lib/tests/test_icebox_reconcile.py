"""icebox_reconcile の3レーン決定論分類テスト（#352）。

観測不能な条件文（reopen-when ブロック無し / source 未実装）はレーン2、成立はレーン1、
凍結180日超で成立なし・本文未更新はレーン3。いずれも LLM 非依存・gh 呼び出しなし。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import icebox_reconcile as ir  # noqa: E402

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _body(block_yaml: str, heading: str = ir.REOPEN_HEADING) -> str:
    return f"## 問題\n本文\n\n{heading}\n\n```yaml\n{block_yaml}\n```\n"


VALID_BLOCK = (
    "reopen-when:\n"
    "  source: weak_signals\n"
    "  metric: unprocessed_count\n"
    '  op: ">"\n'
    "  threshold: 5\n"
)


# ─────────────────────────────────────────────────────────────────
# extract_reopen_when
# ─────────────────────────────────────────────────────────────────
class TestExtractReopenWhen:
    def test_none_body(self):
        assert ir.extract_reopen_when(None) is None

    def test_empty_body(self):
        assert ir.extract_reopen_when("") is None

    def test_no_heading(self):
        body = "## 問題\n本文のみ\n"
        assert ir.extract_reopen_when(body) is None

    def test_valid_block(self):
        block = ir.extract_reopen_when(_body(VALID_BLOCK))
        assert block == {
            "source": "weak_signals",
            "metric": "unprocessed_count",
            "op": ">",
            "threshold": 5,
        }

    def test_missing_required_key(self):
        bad = "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
        assert ir.extract_reopen_when(_body(bad)) is None

    def test_unknown_op(self):
        bad = (
            "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
            '  op: "~="\n  threshold: 1\n'
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    def test_malformed_yaml(self):
        body = f"{ir.REOPEN_HEADING}\n\n```yaml\nreopen-when: [unclosed\n```\n"
        assert ir.extract_reopen_when(body) is None

    def test_non_dict_yaml(self):
        body = f"{ir.REOPEN_HEADING}\n\n```yaml\n- just\n- a\n- list\n```\n"
        assert ir.extract_reopen_when(body) is None

    def test_fence_without_language_tag(self):
        body = f"{ir.REOPEN_HEADING}\n\n```\n{VALID_BLOCK}```\n"
        block = ir.extract_reopen_when(body)
        assert block is not None
        assert block["source"] == "weak_signals"

    def test_block_after_next_heading_is_ignored(self):
        # 再開条件見出しの後に別の見出しがあり、その先に fenced block があっても拾わない。
        body = (
            f"{ir.REOPEN_HEADING}\n\n自由文のみ、YAML 無し\n\n"
            f"## 別セクション\n\n```yaml\n{VALID_BLOCK}```\n"
        )
        assert ir.extract_reopen_when(body) is None

    def test_optional_extra_keys_preserved(self):
        block_yaml = VALID_BLOCK + "  agent_type: senpai\n"
        block = ir.extract_reopen_when(_body(block_yaml))
        assert block["agent_type"] == "senpai"


# ─────────────────────────────────────────────────────────────────
# evaluators
# ─────────────────────────────────────────────────────────────────
class TestWeakSignalsEvaluator:
    def test_counts_unpromoted_nonexpired_only(self, tmp_path):
        import json

        store = tmp_path / "weak_signals.jsonl"
        recs = [
            {
                "promoted": False,
                "detected_at": NOW.isoformat(),
                "channel": "llm_judge",
                "pj_slug": ir.SELF_PJ_SLUG,
            },
            {
                "promoted": True,
                "detected_at": NOW.isoformat(),
                "channel": "llm_judge",
                "pj_slug": ir.SELF_PJ_SLUG,
            },
            {
                "promoted": False,
                "detected_at": (NOW - timedelta(days=200)).isoformat(),
                "channel": "llm_judge",
                "pj_slug": ir.SELF_PJ_SLUG,
            },  # expired (TTL 45日)
            {
                "promoted": False,
                "detected_at": NOW.isoformat(),
                "channel": "llm_judge",
                "pj_slug": "some-other-pj",
            },  # 別 PJ はスコープ外
        ]
        with open(store, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](tmp_path, {})
        assert value == 1.0

    def test_missing_store_returns_zero(self, tmp_path):
        value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](tmp_path, {})
        assert value == 0.0


class TestSubagentTracesEvaluator:
    def test_returns_none_without_data(self, tmp_path):
        value = ir.EVALUATORS["subagent_traces"]["first_try_success_rate"](
            tmp_path, {"agent_type": "senpai"}
        )
        assert value is None

    def test_specific_agent_type(self, tmp_path, monkeypatch):
        def fake_summary(slug, *, min_traces=3, data_dir=None):
            return [
                {"agent_type": "senpai", "n": 5, "first_try_success_rate": 0.8},
                {"agent_type": "explore", "n": 5, "first_try_success_rate": 0.2},
            ]

        import subagent_traces.query as q

        monkeypatch.setattr(q, "per_agent_type_summary", fake_summary)
        value = ir.EVALUATORS["subagent_traces"]["first_try_success_rate"](
            tmp_path, {"agent_type": "senpai"}
        )
        assert value == 0.8

    def test_unknown_agent_type_returns_none(self, tmp_path, monkeypatch):
        def fake_summary(slug, *, min_traces=3, data_dir=None):
            return [{"agent_type": "senpai", "n": 5, "first_try_success_rate": 0.8}]

        import subagent_traces.query as q

        monkeypatch.setattr(q, "per_agent_type_summary", fake_summary)
        value = ir.EVALUATORS["subagent_traces"]["first_try_success_rate"](
            tmp_path, {"agent_type": "nonexistent"}
        )
        assert value is None

    def test_no_agent_type_uses_weighted_average(self, tmp_path, monkeypatch):
        def fake_summary(slug, *, min_traces=3, data_dir=None):
            return [
                {"agent_type": "senpai", "n": 3, "first_try_success_rate": 1.0},
                {"agent_type": "explore", "n": 1, "first_try_success_rate": 0.0},
            ]

        import subagent_traces.query as q

        monkeypatch.setattr(q, "per_agent_type_summary", fake_summary)
        value = ir.EVALUATORS["subagent_traces"]["first_try_success_rate"](tmp_path, {})
        assert value == 0.75


class TestTokenUsageEvaluator:
    def test_no_duckdb_returns_none(self, tmp_path, monkeypatch):
        import token_usage_store as store

        monkeypatch.setattr(store, "HAS_DUCKDB", False)
        value = ir.EVALUATORS["token_usage"]["total_tokens"](tmp_path, {})
        assert value is None

    def test_no_db_file_returns_none(self, tmp_path, monkeypatch):
        import token_usage_store as store

        monkeypatch.setattr(store, "HAS_DUCKDB", True)
        monkeypatch.setattr(store, "USAGE_DB", tmp_path / "no-such.db")
        value = ir.EVALUATORS["token_usage"]["total_tokens"](tmp_path, {})
        assert value is None


# ─────────────────────────────────────────────────────────────────
# classify_issue — 3レーン
# ─────────────────────────────────────────────────────────────────
def _fake_evaluators(value):
    return {"weak_signals": {"unprocessed_count": lambda data_dir, extra: value}}


class TestClassifyIssue:
    def test_lane_met(self, tmp_path):
        issue = {
            "number": 100,
            "body": _body(VALID_BLOCK),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(10)
        )
        assert v["lane"] == "met"
        assert v["number"] == 100
        assert v["value"] == 10
        assert "根拠" not in v  # reason フィールド名は "reason"
        assert v["reason"]

    def test_lane_not_met_and_not_old_is_no_lane(self, tmp_path):
        issue = {
            "number": 101,
            "body": _body(VALID_BLOCK),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(1)
        )
        assert v["lane"] is None

    def test_lane_observer_missing_no_block(self, tmp_path):
        issue = {
            "number": 102,
            "body": "## 問題\n自由文のみ\n",
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(issue, now=NOW, data_dir=tmp_path)
        assert v["lane"] == "observer_missing"
        assert v["source"] is None

    def test_lane_observer_missing_unknown_source(self, tmp_path):
        block_yaml = (
            "reopen-when:\n  source: totally_unknown_source\n"
            '  metric: whatever\n  op: ">"\n  threshold: 1\n'
        )
        issue = {
            "number": 103,
            "body": _body(block_yaml),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(issue, now=NOW, data_dir=tmp_path)
        assert v["lane"] == "observer_missing"
        assert v["source"] == "totally_unknown_source"

    def test_lane_observer_missing_unknown_metric(self, tmp_path):
        block_yaml = (
            "reopen-when:\n  source: weak_signals\n"
            '  metric: totally_unknown_metric\n  op: ">"\n  threshold: 1\n'
        )
        issue = {
            "number": 104,
            "body": _body(block_yaml),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(issue, now=NOW, data_dir=tmp_path)
        assert v["lane"] == "observer_missing"

    def test_lane_archive_candidate(self, tmp_path):
        closed = NOW - timedelta(days=200)
        issue = {
            "number": 105,
            "body": _body(VALID_BLOCK),
            "closedAt": closed.isoformat(),
            "updatedAt": closed.isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(1)
        )
        assert v["lane"] == "archive_candidate"
        assert v["days_since_closed"] >= ir.ARCHIVE_AGE_DAYS

    def test_archive_candidate_excluded_when_body_edited_after_close(self, tmp_path):
        closed = NOW - timedelta(days=200)
        updated = NOW - timedelta(days=5)  # 最近本文が編集された
        issue = {
            "number": 106,
            "body": _body(VALID_BLOCK),
            "closedAt": closed.isoformat(),
            "updatedAt": updated.isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(1)
        )
        assert v["lane"] is None

    def test_met_takes_priority_over_archive_age(self, tmp_path):
        closed = NOW - timedelta(days=200)
        issue = {
            "number": 107,
            "body": _body(VALID_BLOCK),
            "closedAt": closed.isoformat(),
            "updatedAt": closed.isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(10)
        )
        assert v["lane"] == "met"

    def test_observer_missing_even_if_old(self, tmp_path):
        """ブロック無し issue は年齢に関わらずレーン2（レーン3に流さない・仕様通り）。"""
        closed = NOW - timedelta(days=400)
        issue = {
            "number": 108,
            "body": "## 問題\n自由文のみ\n",
            "closedAt": closed.isoformat(),
            "updatedAt": closed.isoformat(),
        }
        v = ir.classify_issue(issue, now=NOW, data_dir=tmp_path)
        assert v["lane"] == "observer_missing"

    def test_evaluator_returns_none_does_not_crash(self, tmp_path):
        issue = {
            "number": 109,
            "body": _body(VALID_BLOCK),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(None)
        )
        assert v["lane"] is None
        assert v["value"] is None

    def test_missing_closed_at_does_not_crash(self, tmp_path):
        issue = {"number": 110, "body": _body(VALID_BLOCK)}
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(1)
        )
        assert v["days_since_closed"] is None
        assert v["lane"] is None


# ─────────────────────────────────────────────────────────────────
# build_verdicts
# ─────────────────────────────────────────────────────────────────
class TestBuildVerdicts:
    def test_builds_payload_for_all_issues(self, tmp_path):
        issues = [
            {
                "number": 1,
                "body": _body(VALID_BLOCK),
                "closedAt": (NOW - timedelta(days=10)).isoformat(),
                "updatedAt": (NOW - timedelta(days=10)).isoformat(),
            },
            {"number": 2, "body": "## 問題\n自由文\n"},
        ]
        payload = ir.build_verdicts(
            issues, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(10)
        )
        assert payload["generated_at"] == NOW.isoformat()
        assert len(payload["verdicts"]) == 2
        numbers = {v["number"] for v in payload["verdicts"]}
        assert numbers == {1, 2}

    def test_skips_non_dict_issues(self, tmp_path):
        payload = ir.build_verdicts(
            [{"number": 1, "body": ""}, "not-a-dict", None],
            now=NOW,
            data_dir=tmp_path,
        )
        assert len(payload["verdicts"]) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
