#!/usr/bin/env python3
"""#103: 情報レーン advisory の dismiss / 抑制（suppression ledger 一般化）。

#477-2 が個別承認レーン（proposable_custom_individual）に配線した suppression ledger を、
情報レーンの advisory（rule_violation_observed / proposable_global / prune.global_candidates）
にも効かせるための一般化ヘルパのテスト。既存 `remediation_suppression/<slug>.jsonl` ストアを
**そのまま再利用**し、lane + identity を issue 形（type/file/detail）に整形して既存 dedup_key /
record_rejection / is_suppressed に委譲する（新ストアを増やさない）。

PJスコープ・TTL45日・冪等は #477-2 を踏襲。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
sys.path.insert(0, str(_lib_dir))

import remediation.suppression_ledger as sl  # noqa: E402


def _rule_violation(cmd, count=142):
    """rule_violation_observed レーンの 1 項目（rule_violation_lane の出力形）。"""
    return {
        "pattern": cmd,
        "violated_command": cmd,
        "count": count,
        "reason": "rule_installed_but_not_enforced",
        "recommendation": f"`{cmd}` は禁止済みだが {count} 回観測。hook enforce を検討。",
    }


# ── make_advisory_issue: lane + identity を issue 形に整形 ─────────
class TestMakeAdvisoryIssue:
    def test_shapes_lane_and_identity(self):
        iss = sl.make_advisory_issue("rule_violation_observed", "cd")
        assert iss["type"] == "rule_violation_observed"
        assert iss["detail"]["name"] == "cd"

    def test_stable_dedup_key_for_same_lane_identity(self):
        a = sl.make_advisory_issue("rule_violation_observed", "cd")
        b = sl.make_advisory_issue("rule_violation_observed", "cd")
        assert sl.dedup_key(a) == sl.dedup_key(b)

    def test_differs_by_identity(self):
        a = sl.make_advisory_issue("rule_violation_observed", "cd")
        b = sl.make_advisory_issue("rule_violation_observed", "grep")
        assert sl.dedup_key(a) != sl.dedup_key(b)

    def test_differs_by_lane(self):
        a = sl.make_advisory_issue("rule_violation_observed", "cd")
        b = sl.make_advisory_issue("prune_global_candidates", "cd")
        assert sl.dedup_key(a) != sl.dedup_key(b)


# ── record / suppress roundtrip（advisory 経路） ─────────────────
class TestAdvisoryRecordSuppress:
    def test_record_then_suppressed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        iss = sl.make_advisory_issue("rule_violation_observed", "cd")
        assert not sl.is_suppressed(iss, slug="proj")
        sl.record_rejection(iss, slug="proj")
        assert sl.is_suppressed(iss, slug="proj")

    def test_per_slug_isolation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        iss = sl.make_advisory_issue("rule_violation_observed", "cd")
        sl.record_rejection(iss, slug="proj-a")
        assert sl.is_suppressed(iss, slug="proj-a")
        assert not sl.is_suppressed(iss, slug="proj-b")

    def test_ttl_expiry_resurfaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        iss = sl.make_advisory_issue("rule_violation_observed", "cd")
        sl.record_rejection(iss, slug="proj", now=1000.0, ttl_days=45)
        assert sl.is_suppressed(iss, slug="proj", now=1000.0 + 10 * 86400)
        assert not sl.is_suppressed(iss, slug="proj", now=1000.0 + 46 * 86400)

    def test_dry_run_no_write(self, tmp_path, monkeypatch):
        root = tmp_path / "remediation_suppression"
        monkeypatch.setattr(sl, "LEDGER_ROOT", root)
        iss = sl.make_advisory_issue("rule_violation_observed", "cd")
        sl.record_rejection(iss, slug="proj", persist=False)
        assert not root.exists() or list(root.glob("*.jsonl")) == []
        assert not sl.is_suppressed(iss, slug="proj")


# ── filter_suppressed_advisories: 生 item を lane+identity で2分割 ──
class TestFilterSuppressedAdvisories:
    def _ident(self, v):
        return str(v.get("violated_command", ""))

    def test_splits_surface_and_suppressed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        keep = _rule_violation("grep")
        dropped = _rule_violation("cd")
        # cd を「このPJでは意図的運用」として却下記録
        sl.record_rejection(
            sl.make_advisory_issue("rule_violation_observed", "cd"), slug="proj"
        )
        out = sl.filter_suppressed_advisories(
            [keep, dropped],
            lane="rule_violation_observed",
            identity_of=self._ident,
            slug="proj",
        )
        # 生 item の原型を保ったまま分割される
        assert out["surface"] == [keep]
        assert out["suppressed"] == [dropped]

    def test_empty_identity_always_surfaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        noid = {"violated_command": "", "count": 3}
        out = sl.filter_suppressed_advisories(
            [noid], lane="rule_violation_observed", identity_of=self._ident, slug="proj"
        )
        assert out["surface"] == [noid]
        assert out["suppressed"] == []

    def test_ttl_expiry_resurfaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        item = _rule_violation("cd")
        sl.record_rejection(
            sl.make_advisory_issue("rule_violation_observed", "cd"),
            slug="proj",
            now=1000.0,
            ttl_days=45,
        )
        within = sl.filter_suppressed_advisories(
            [item], lane="rule_violation_observed", identity_of=self._ident,
            slug="proj", now=1000.0 + 10 * 86400,
        )
        assert within["suppressed"] == [item]
        after = sl.filter_suppressed_advisories(
            [item], lane="rule_violation_observed", identity_of=self._ident,
            slug="proj", now=1000.0 + 46 * 86400,
        )
        assert after["surface"] == [item]

    def test_read_only_no_write(self, tmp_path, monkeypatch):
        root = tmp_path / "remediation_suppression"
        monkeypatch.setattr(sl, "LEDGER_ROOT", root)
        sl.filter_suppressed_advisories(
            [_rule_violation("cd")],
            lane="rule_violation_observed",
            identity_of=self._ident,
            slug="proj",
        )
        assert not root.exists() or list(root.glob("*.jsonl")) == []

    def test_prune_global_summary_lane(self, tmp_path, monkeypatch):
        """prune.global_candidates の件数サマリ全体を 1 identity で dismiss できる。"""
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        adv = sl.make_advisory_issue("prune_global_candidates", "summary")
        assert not sl.is_suppressed(adv, slug="proj")
        sl.record_rejection(adv, slug="proj")
        assert sl.is_suppressed(adv, slug="proj")
