"""evolve_consistency のテスト（#377-5・決定論・LLM 非依存）。

P1（#375/#376）で導入した invariant を runtime で consume し、evolve の result から
1) CANONICAL とのキー/型乖離（impl drift）
2) usage_count==0 なのに suitability∈{high,medium}（usage↔suitability 矛盾）
を self-detect candidate として surface する。健全な result では 0 件＝regression guard。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "lib"))

import evolve_consistency as ec  # noqa: E402


def _clean() -> dict:
    """CANONICAL 準拠かつ矛盾なしの最小 result。"""
    return {
        "phases": {
            "remediation": {
                "total_issues": 0, "auto_fixable": 0, "proposable": 0,
                "proposable_custom": 0, "proposable_global": 0, "manual_required": 0,
                "classified": {
                    "proposable": [], "auto_fixable": [], "manual_required": [],
                    "proposable_custom": [], "proposable_global": [],
                },
            },
            "skill_evolve": {
                "assessments": [], "total_skills": 0, "high_suitability": 0,
                "medium_suitability": 0, "insufficient_usage": 0, "rejected": 0,
                "batch_guard_trigger": None,
            },
            "skill_triage": {
                "CREATE": [], "UPDATE": [], "SPLIT": [], "MERGE": [], "OK": [],
                "SKIP_SUPPRESSED": [], "skip_suppressed_summary": "",
            },
        }
    }


# ── 構造契約: section 形 + 0 件 summary_line ──────────────


def test_clean_result_zero_candidates():
    section = ec.detect_consistency_drift(_clean())
    assert section["candidates"] == []
    assert "✓" in section["summary_line"]


def test_empty_result_does_not_crash():
    section = ec.detect_consistency_drift({})
    assert section["candidates"] == []
    assert isinstance(section["summary_line"], str)


# ── ① CANONICAL kind 不一致 → candidate ───────────────────


def test_conformance_violation_becomes_candidate():
    r = _clean()
    r["phases"]["remediation"]["proposable"] = [{"type": "x"}]  # int 期待に list
    section = ec.detect_consistency_drift(r)
    assert section["candidates"], "kind 不一致が candidate 化されていない"
    assert any(
        "proposable" in c["dedup_key"] and "conformance" in c["dedup_key"]
        for c in section["candidates"]
    )


def test_conformance_candidate_shape():
    r = _clean()
    r["phases"]["remediation"]["proposable"] = [{"type": "x"}]  # wrong_kind（int 期待に list）
    c = ec.detect_consistency_drift(r)["candidates"][0]
    for key in ("category", "title", "body", "suggested_label", "dedup_key", "severity"):
        assert key in c
    assert c["category"] == "improvement"


def test_missing_key_is_not_runtime_candidate():
    """missing は runtime では FP ノイズ源として除外（型 drift のみ拾う）。"""
    r = _clean()
    del r["phases"]["remediation"]["proposable"]  # missing
    section = ec.detect_consistency_drift(r)
    assert not any("conformance" in c["dedup_key"] for c in section["candidates"])


# ── ② usage0 × suitability 矛盾 → candidate（regression guard） ──


def test_usage0_with_medium_suitability_is_contradiction():
    r = _clean()
    r["phases"]["skill_evolve"]["assessments"] = [
        {"skill_name": "x", "suitability": "medium", "telemetry_detail": {"usage_count": 0}},
    ]
    section = ec.detect_consistency_drift(r)
    assert any("usage_suitability" in c["dedup_key"] for c in section["candidates"])


def test_usage0_with_high_suitability_is_contradiction():
    r = _clean()
    r["phases"]["skill_evolve"]["assessments"] = [
        {"skill_name": "y", "suitability": "high", "telemetry_detail": {"usage_count": 0}},
    ]
    section = ec.detect_consistency_drift(r)
    subjects = [c.get("subject") for c in section["candidates"]]
    assert "y" in subjects


def test_usage0_with_insufficient_usage_is_not_contradiction():
    """P1(#376) の修正後はこの形になる＝矛盾なし（regression guard）。"""
    r = _clean()
    r["phases"]["skill_evolve"]["assessments"] = [
        {"skill_name": "z", "suitability": "insufficient_usage",
         "telemetry_detail": {"usage_count": 0}},
    ]
    section = ec.detect_consistency_drift(r)
    assert not any("usage_suitability" in c["dedup_key"] for c in section["candidates"])


def test_usage_present_with_medium_is_not_contradiction():
    r = _clean()
    r["phases"]["skill_evolve"]["assessments"] = [
        {"skill_name": "w", "suitability": "medium", "telemetry_detail": {"usage_count": 5}},
    ]
    assert ec.detect_consistency_drift(r)["candidates"] == []


def test_dedup_stable_for_same_skill():
    r = _clean()
    r["phases"]["skill_evolve"]["assessments"] = [
        {"skill_name": "dup", "suitability": "high", "telemetry_detail": {"usage_count": 0}},
        {"skill_name": "dup", "suitability": "high", "telemetry_detail": {"usage_count": 0}},
    ]
    keys = [c["dedup_key"] for c in ec.detect_consistency_drift(r)["candidates"]]
    assert len(keys) == len(set(keys))
