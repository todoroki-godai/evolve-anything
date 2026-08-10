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

    # ── B1: untrusted YAML の型エラーでクラッシュしない ──────────────
    def test_non_str_op_does_not_crash(self):
        bad = (
            "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
            "  op: []\n  threshold: 1\n"
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    def test_non_str_source_does_not_crash(self):
        bad = (
            "reopen-when:\n  source: []\n  metric: unprocessed_count\n"
            '  op: ">"\n  threshold: 1\n'
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    def test_non_str_metric_does_not_crash(self):
        bad = (
            "reopen-when:\n  source: weak_signals\n  metric: {}\n"
            '  op: ">"\n  threshold: 1\n'
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    # ── B4: source/metric の injection 耐性（制御文字・改行・過長は拒否） ──
    def test_source_with_newline_is_rejected(self):
        bad = (
            "reopen-when:\n"
            '  source: "weak_signals\\n## 別見出し\\n悪意のある本文"\n'
            '  metric: unprocessed_count\n  op: ">"\n  threshold: 1\n'
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    def test_metric_too_long_is_rejected(self):
        bad = (
            "reopen-when:\n  source: weak_signals\n"
            f"  metric: {'a' * 65}\n"
            '  op: ">"\n  threshold: 1\n'
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    def test_source_with_spaces_is_rejected(self):
        bad = (
            "reopen-when:\n  source: not a valid token\n"
            '  metric: unprocessed_count\n  op: ">"\n  threshold: 1\n'
        )
        assert ir.extract_reopen_when(_body(bad)) is None

    # ── P4: 複数 reopen-when ブロックは ambiguous ──────────────────
    def test_multiple_valid_blocks_first_wins_for_extract(self):
        """extract_reopen_when 単体は従来どおり先頭優先（ambiguous 判定は classify_issue 側）。"""
        body = (
            f"{ir.REOPEN_HEADING}\n\n```yaml\n{VALID_BLOCK}```\n\n"
            f"```yaml\n{VALID_BLOCK}```\n"
        )
        block = ir.extract_reopen_when(body)
        assert block is not None


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


# ── B6: pj_slug 上書きの廃止（クロス PJ 情報開示防止） ────────────────
class TestPjSlugOverrideRemoved:
    def test_weak_signals_evaluator_ignores_extra_pj_slug(self, tmp_path):
        import json

        store = tmp_path / "weak_signals.jsonl"
        recs = [
            {
                "promoted": False,
                "detected_at": NOW.isoformat(),
                "channel": "llm_judge",
                "pj_slug": "some-other-pj",
            },
        ]
        with open(store, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        # extra で他 PJ の pj_slug を指定しても無視され、SELF_PJ_SLUG スコープのまま
        # （このレコードは対象外なので 0 件のはず）。
        value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](
            tmp_path, {"pj_slug": "some-other-pj"}
        )
        assert value == 0.0

    def test_subagent_traces_evaluator_ignores_extra_pj_slug(self, tmp_path, monkeypatch):
        captured_slug = {}

        def fake_summary(slug, *, min_traces=3, data_dir=None):
            captured_slug["slug"] = slug
            return [{"agent_type": "senpai", "n": 5, "first_try_success_rate": 0.8}]

        import subagent_traces.query as q

        monkeypatch.setattr(q, "per_agent_type_summary", fake_summary)
        ir.EVALUATORS["subagent_traces"]["first_try_success_rate"](
            tmp_path, {"agent_type": "senpai", "pj_slug": "some-other-pj"}
        )
        assert captured_slug["slug"] == ir.SELF_PJ_SLUG


# ── B9: weak_signals evaluator の union read ─────────────────────────
class TestWeakSignalsEvaluatorUnionRead:
    def test_counts_legacy_dir_records_too(self, tmp_path, monkeypatch):
        """DATA_DIR 分裂時、canonical だけでなく legacy dir のレコードも合算する。"""
        import json

        import rl_common

        canonical = tmp_path / "canonical"
        legacy = tmp_path / "legacy"
        canonical.mkdir()
        legacy.mkdir()
        monkeypatch.setattr(
            rl_common, "iter_read_data_dirs", lambda canonical=None: [canonical, legacy]
        )
        with open(legacy / "weak_signals.jsonl", "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "promoted": False,
                        "detected_at": NOW.isoformat(),
                        "channel": "llm_judge",
                        "pj_slug": ir.SELF_PJ_SLUG,
                        "signal_key": "legacy-1",
                    }
                )
                + "\n"
            )
        value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](canonical, {})
        assert value == 1.0

    def test_reviewed_in_legacy_dir_is_excluded_from_actionable(self, tmp_path, monkeypatch):
        """#405 round6 [Must]2: 既読ストアも weak_signal と同じ dir 集合で union read する。

        weak_signal・既読(rejected) の両方を legacy dir に置いた構成で、read 範囲が
        weak_signal 側だけ union・既読側は canonical 単一のまま（round5 初回実装のバグ）だと
        既読が見えず actionable として復活し value=1.0 になる。両方 union read すれば
        0.0 になる。
        """
        import json

        import rl_common

        canonical = tmp_path / "canonical"
        legacy = tmp_path / "legacy"
        canonical.mkdir()
        legacy.mkdir()
        monkeypatch.setattr(
            rl_common, "iter_read_data_dirs", lambda canonical=None: [canonical, legacy]
        )
        signal_key = "legacy-reviewed-1"
        with open(legacy / "weak_signals.jsonl", "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "promoted": False,
                        "detected_at": NOW.isoformat(),
                        "channel": "llm_judge",
                        "pj_slug": ir.SELF_PJ_SLUG,
                        "signal_key": signal_key,
                    }
                )
                + "\n"
            )
        with open(legacy / "correction_review_seen.jsonl", "w", encoding="utf-8") as f:
            f.write(
                json.dumps({
                    "key": signal_key, "pj_slug": ir.SELF_PJ_SLUG,
                    "decision": "rejected", "reviewed_at": NOW.isoformat(),
                })
                + "\n"
            )
        value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](canonical, {})
        assert value == 0.0


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

    # ── B1: evaluator が例外を投げても classify_issue は落ちない ─────────
    def test_evaluator_raising_exception_does_not_crash(self, tmp_path):
        def _boom(data_dir, extra):
            raise TypeError("cannot use 'list' as a dict key")

        issue = {
            "number": 111,
            "body": _body(VALID_BLOCK),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue,
            now=NOW,
            data_dir=tmp_path,
            evaluators={"weak_signals": {"unprocessed_count": _boom}},
        )
        assert v["lane"] is None
        assert v["value"] is None

    # ── B2: NaN/inf threshold は成立にならない ───────────────────────
    def test_nan_threshold_is_observer_missing(self, tmp_path):
        block_yaml = (
            "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
            '  op: "!="\n  threshold: .nan\n'
        )
        issue = {
            "number": 112,
            "body": _body(block_yaml),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(5)
        )
        assert v["lane"] == "observer_missing"

    def test_inf_threshold_is_observer_missing(self, tmp_path):
        block_yaml = (
            "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
            '  op: "<"\n  threshold: .inf\n'
        )
        issue = {
            "number": 113,
            "body": _body(block_yaml),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(5)
        )
        assert v["lane"] == "observer_missing"

    def test_non_numeric_threshold_reason_does_not_echo_raw_value(self, tmp_path):
        """B4: threshold が数値でない場合、issue 本文由来の生値を reason に埋め込まない。"""
        block_yaml = (
            "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
            '  op: ">"\n  threshold: "AAAA\\n## injected heading\\npwned"\n'
        )
        issue = {
            "number": 114,
            "body": _body(block_yaml),
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(issue, now=NOW, data_dir=tmp_path)
        assert v["lane"] == "observer_missing"
        assert "pwned" not in v["reason"]
        assert "injected" not in v["reason"]

    # ── B3: 評価値未計測（None）は archive 候補にしない ────────────────
    def test_old_closed_with_none_value_is_not_archive_candidate(self, tmp_path):
        closed = NOW - timedelta(days=200)
        issue = {
            "number": 115,
            "body": _body(VALID_BLOCK),
            "closedAt": closed.isoformat(),
            "updatedAt": closed.isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(None)
        )
        assert v["lane"] is None
        assert v["reason"] == "評価値を取得できませんでした（未計測）"

    # ── P4: 複数 reopen-when ブロックは ambiguous として observer_missing ──
    def test_multiple_reopen_when_blocks_is_ambiguous(self, tmp_path):
        body = (
            f"{ir.REOPEN_HEADING}\n\n```yaml\n{VALID_BLOCK}```\n\n"
            f"```yaml\n{VALID_BLOCK}```\n"
        )
        issue = {
            "number": 116,
            "body": body,
            "closedAt": (NOW - timedelta(days=10)).isoformat(),
            "updatedAt": (NOW - timedelta(days=10)).isoformat(),
        }
        v = ir.classify_issue(
            issue, now=NOW, data_dir=tmp_path, evaluators=_fake_evaluators(999)
        )
        assert v["lane"] == "observer_missing"
        assert "複数" in v["reason"]


# ─────────────────────────────────────────────────────────────────
# P3: _edited_after_close の窓（実質48時間バグの回帰テスト）
# ─────────────────────────────────────────────────────────────────
class TestEditedAfterClose:
    def test_25_hours_after_close_counts_as_edited(self):
        """UPDATE_TOLERANCE_DAYS=1（=24h）超なら編集済みとみなす。
        `.days` 比較の丸めバグは 25h 差を「未編集」に誤判定していた（実質48h窓）。"""
        closed = NOW
        updated = NOW + timedelta(hours=25)
        assert ir._edited_after_close(closed, updated) is True

    def test_exactly_24_hours_is_not_edited(self):
        closed = NOW
        updated = NOW + timedelta(hours=24)
        assert ir._edited_after_close(closed, updated) is False

    def test_47_hours_counts_as_edited(self):
        closed = NOW
        updated = NOW + timedelta(hours=47)
        assert ir._edited_after_close(closed, updated) is True


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
