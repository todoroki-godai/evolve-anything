"""evolve_result_schema の契約テスト（#375・決定論・LLM 非依存）。

2 層で result↔doc のキー乖離を封じる:
  1. check_conformance — 実 dry-run の result が CANONICAL に一致するか（impl 側 drift）
  2. extract_documented_paths ⊆ canonical_paths — SKILL.md の dotted path が
     全て canonical に存在するか（doc 側 drift）

impl 側テストは合成 fixture でなく **実 run_evolve(dry_run=True)** の出力で検証する
（合成 fixture は自作の前提どおりだと緑になり実構造の乖離を見逃すため、verify-data-contract）。
"""
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent  # <repo>/scripts
sys.path.insert(0, str(_ROOT / "lib"))
sys.path.insert(0, str(_ROOT.parent / "skills" / "evolve" / "scripts"))

import evolve_result_schema as ers  # noqa: E402

# run_evolve は project_dir=_REPO でも post-processing フェーズが
# Path.home()/.claude/projects（実 store ≈9925 jsonl）を走査するため、別 conftest
# スコープ（skills/evolve/scripts/tests/conftest.py の autouse）の恩恵が及ばない。
# 同じ HOME 隔離 helper（scripts/lib/test_home_isolation.py — conftest 名衝突回避の
# ため専用モジュール）を import して本ファイルでも明示適用する（#457）。

_REPO = _ROOT.parent  # <repo>
_SKILL_MD = _REPO / "skills" / "evolve" / "SKILL.md"


# ── 1. check_conformance のユニット（関数ロジック） ──────────────


def _minimal_valid() -> dict:
    """全 required キーを満たす最小 result。"""
    return {
        "phases": {
            "remediation": {
                "total_issues": 0, "auto_fixable": 0, "proposable": 0,
                "proposable_custom": 0, "proposable_global": 0, "manual_required": 0,
                "proposable_custom_individual": 0, "proposable_custom_batch_skip": 0,
                "suppressed_by_ledger": 0, "auto_rejected_by_reconcile": 0,
                "proposable_global_suppressed": 0, "rule_violation_suppressed": 0,
                "classified": {
                    "proposable": [], "auto_fixable": [], "manual_required": [],
                    "proposable_custom": [], "proposable_global": [],
                    "proposable_custom_individual": [], "proposable_custom_batch_skip": [],
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
        },
        # top-level required キー（#493）。phases と並ぶ正準フィールド。
        "correction_review": {"bootstrap": {}, "daily": {}},
        "correction_semantic": {},
        "evolve_decisions": {"pending": []},
        "growth_report": {},
        "idiom_autopromote": {},
        "observability": {},
        "self_analysis": {},
        "trigger_summary": {},
        "warnings": [],
        "weak_signals": {},
        "weak_signals_ttl": {},
        "env_tier": "small",
        "env_tier_reason": {},
        "slug": "x",
        "project_dir": "/x",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "dry_run": True,
    }


def test_minimal_valid_conforms():
    assert ers.check_conformance(_minimal_valid()) == []


def test_missing_required_key_is_violation():
    r = _minimal_valid()
    del r["phases"]["remediation"]["proposable"]
    viol = ers.check_conformance(r)
    assert any("missing: phases.remediation.proposable" in v for v in viol)


def test_proposable_must_be_int_not_list():
    """#375 の核: proposable は件数(int)。誤って list を入れたら違反。"""
    r = _minimal_valid()
    r["phases"]["remediation"]["proposable"] = [{"type": "x"}]
    viol = ers.check_conformance(r)
    assert any("wrong kind: phases.remediation.proposable" in v for v in viol)


def test_bool_rejected_where_int_expected():
    r = _minimal_valid()
    r["phases"]["remediation"]["proposable"] = True
    viol = ers.check_conformance(r)
    assert any("got bool" in v for v in viol)


def test_skipped_phase_optional_keys_not_required():
    """reorganize skipped 時は split_candidates 欠落でも違反にしない。"""
    r = _minimal_valid()
    r["phases"]["reorganize"] = {"skipped": True, "reason": "insufficient_skills", "count": 0}
    assert ers.check_conformance(r) == []


def test_error_phase_skips_its_keys():
    r = _minimal_valid()
    r["phases"]["remediation"] = {"error": "boom"}
    assert ers.check_conformance(r) == []


def test_split_candidate_item_keys_enforced():
    """split_candidates の item は skill_name/line_count（#375 の .skill/.content_lines 誤検出）。"""
    r = _minimal_valid()
    r["phases"]["reorganize"] = {
        "skipped": False,
        "split_candidates": [{"skill": "x", "content_lines": 90}],  # 誤キー
    }
    viol = ers.check_conformance(r)
    assert any("item key missing: phases.reorganize.split_candidates" in v for v in viol)


def test_nullable_batch_guard_allows_none():
    r = _minimal_valid()
    r["phases"]["skill_evolve"]["batch_guard_trigger"] = None
    assert ers.check_conformance(r) == []


# ── 2. 実 dry-run の dogfood（impl 側 drift の本命ガード） ────────


@pytest.mark.skipif(
    not (_REPO / "skills" / "evolve" / "scripts" / "evolve" / "__init__.py").exists(),
    reason="evolve パッケージ不在",
)
def test_real_dry_run_result_conforms():
    """実 run_evolve(dry_run=True) の出力が CANONICAL に準拠する（合成 fixture を使わない）。

    dry-run は ADR-037 で LLM-free（cache-read + 決定論）なので no-llm-in-tests に抵触しない。
    """
    sys.path.insert(0, str(_REPO / "skills" / "evolve" / "scripts"))
    import evolve  # noqa: E402

    result = evolve.run_evolve(project_dir=str(_REPO), dry_run=True)
    viol = ers.check_conformance(result)
    assert viol == [], f"実 result が契約から乖離: {viol}"


# ── 3. doc 側 drift: SKILL.md の dotted path ⊆ canonical ─────────


@pytest.mark.skipif(not _SKILL_MD.exists(), reason="SKILL.md 不在")
def test_skill_md_documented_paths_are_canonical():
    text = _SKILL_MD.read_text(encoding="utf-8")
    documented = ers.extract_documented_paths(text)
    unknown = ers.documented_path_drift(documented)
    assert not unknown, (
        f"SKILL.md が canonical に無い result path を参照（doc drift）: {sorted(unknown)}。"
        f"evolve_result_schema.CANONICAL を更新するか SKILL.md を修正すること"
    )


def test_extract_strips_result_prefix():
    paths = ers.extract_documented_paths("`result.phases.skill_evolve.batch_guard_trigger`")
    assert "phases.skill_evolve.batch_guard_trigger" in paths


# ── 4. 機械可読 conformance（P2 consume 用・#379-5） ──────────────


def test_structured_conformance_returns_path_and_reason():
    r = _minimal_valid()
    del r["phases"]["remediation"]["proposable"]
    viols = ers.check_conformance_structured(r)
    assert any(v.path == "phases.remediation.proposable" and v.reason == "missing" for v in viols)


def test_structured_conformance_wrong_kind_reason():
    r = _minimal_valid()
    r["phases"]["remediation"]["proposable"] = [{"type": "x"}]
    viols = ers.check_conformance_structured(r)
    match = [v for v in viols if v.path == "phases.remediation.proposable"]
    assert match and match[0].reason == "wrong_kind"


def test_structured_conformance_null_reason():
    r = _minimal_valid()
    # batch_guard_trigger は nullable だが、nullable でない int を None にすると null 違反
    r["phases"]["remediation"]["proposable"] = None
    viols = ers.check_conformance_structured(r)
    match = [v for v in viols if v.path == "phases.remediation.proposable"]
    assert match and match[0].reason == "null_not_allowed"


def test_str_check_conformance_wraps_structured():
    """str 版は structured 版の薄いラッパ（後方互換）。同じ違反集合を返す。"""
    r = _minimal_valid()
    del r["phases"]["remediation"]["proposable"]
    structured = ers.check_conformance_structured(r)
    strings = ers.check_conformance(r)
    assert len(strings) == len(structured)
    assert all(s.path in msg for s, msg in zip(structured, strings))


def test_clean_result_has_no_structured_violations():
    assert ers.check_conformance_structured(_minimal_valid()) == []


# ── 5. longest-prefix doc-drift（dict sub-field 誤検出回避・#379-3） ──


def test_documented_subfield_of_dict_key_is_not_drift():
    """dict canonical キーの sub-field を doc 参照しても drift にしない（FP build 破壊を回避）。"""
    drift = ers.documented_path_drift({"phases.skill_evolve.batch_guard_trigger.reason"})
    assert drift == set()


def test_documented_ancestor_of_canonical_is_not_drift():
    drift = ers.documented_path_drift({"phases.skill_evolve"})
    assert drift == set()


def test_truly_unknown_documented_path_is_drift():
    drift = ers.documented_path_drift({"phases.bogus.nonexistent"})
    assert "phases.bogus.nonexistent" in drift


def test_extract_bracket_notation():
    paths = ers.extract_documented_paths('result["phases"]["skill_evolve"]["assessments"]')
    assert "phases.skill_evolve.assessments" in paths


# ── 6. 逆方向契約: phase 集合 ⊆ 既知 phase（#379-1） ───────────────


def test_covered_and_uncovered_phases_are_disjoint():
    assert ers.COVERED_PHASES.isdisjoint(ers.UNCOVERED_PHASES)


@pytest.mark.skipif(
    not (_REPO / "skills" / "evolve" / "scripts" / "evolve" / "__init__.py").exists(),
    reason="evolve パッケージ不在",
)
def test_real_phases_are_all_registered():
    """実 dry-run の phase 集合が COVERED ∪ UNCOVERED に収まる（未登録 phase で fail）。

    CANONICAL は意図的に部分カバー。新 phase 追加時に CANONICAL か UNCOVERED_PHASES の
    更新を強制し、契約自体の静かな陳腐化（#375 が解こうとした drift の構造的再発）を封じる。
    """
    sys.path.insert(0, str(_REPO / "skills" / "evolve" / "scripts"))
    import evolve  # noqa: E402

    result = evolve.run_evolve(project_dir=str(_REPO), dry_run=True)
    actual = set(result.get("phases", {}).keys())
    known = ers.COVERED_PHASES | ers.UNCOVERED_PHASES
    unregistered = actual - known
    assert not unregistered, (
        f"未登録の phase: {sorted(unregistered)}。CANONICAL にキーを追加するか "
        f"UNCOVERED_PHASES に明示登録すること（契約の意図的部分カバーを enforce）"
    )


# ── 6b. 逆方向契約: top-level キー集合 ⊆ 既知 top-level（#493） ──────
#
# phases.* と同型の完全性ゲートを top-level キーにも掛ける。#442-#448 で大量追加された
# correction_review / growth_report / idiom_autopromote 等が schema 契約の対象外で、
# rename / kind drift が素通りし SKILL.md reader が静かに空表示になる構造的再発を封じる。


def test_covered_and_uncovered_toplevel_are_disjoint():
    assert ers.COVERED_TOPLEVEL.isdisjoint(ers.UNCOVERED_TOPLEVEL)


@pytest.mark.skipif(
    not (_REPO / "skills" / "evolve" / "scripts" / "evolve" / "__init__.py").exists(),
    reason="evolve パッケージ不在",
)
def test_real_toplevel_keys_are_all_registered():
    """実 dry-run の top-level キー集合が COVERED ∪ UNCOVERED に収まる（未登録キーで fail）。

    phases.* の completeness（test_real_phases_are_all_registered）と同型。新しい top-level
    キーを足したら CANONICAL に登録するか UNCOVERED_TOPLEVEL に明示登録することを強制し、
    schema 契約自体の静かな陳腐化を封じる（#493）。
    """
    sys.path.insert(0, str(_REPO / "skills" / "evolve" / "scripts"))
    import evolve  # noqa: E402

    result = evolve.run_evolve(project_dir=str(_REPO), dry_run=True)
    actual = {k for k in result.keys() if k != "phases"}
    known = ers.COVERED_TOPLEVEL | ers.UNCOVERED_TOPLEVEL
    unregistered = actual - known
    assert not unregistered, (
        f"未登録の top-level キー: {sorted(unregistered)}。CANONICAL にキーを追加するか "
        f"UNCOVERED_TOPLEVEL に明示登録すること（契約の意図的部分カバーを enforce・#493）"
    )


def test_toplevel_required_keys_are_registered_in_canonical():
    """SKILL.md reader が読む実害キーが CANONICAL に登録されている（#493 範囲補足）。

    issue 本文＋コメントで列挙された reader 必須キー。これらは optional でなく required で
    登録され、rename / 欠落が test-time conformance で検出されること。
    """
    canon = ers.canonical_paths()
    required = {
        "correction_review.bootstrap",
        "correction_review.daily",
        "growth_report",
        "idiom_autopromote",
        "observability",
        "weak_signals",
        "weak_signals_ttl",
        "evolve_decisions",
        "evolve_decisions.pending",
    }
    missing = required - canon
    assert not missing, f"reader 必須 top-level キーが CANONICAL 未登録: {sorted(missing)}"


def test_toplevel_growth_report_kind_drift_detected():
    """growth_report が dict→他型に drift したら conformance が wrong_kind を出す（#493）。"""
    r = _minimal_valid()
    r["growth_report"] = "broken"  # dict であるべき
    viol = ers.check_conformance(r)
    assert any("wrong kind: growth_report" in v for v in viol), viol


def test_toplevel_correction_review_daily_kind_drift_detected():
    """correction_review.daily が dict→他型に drift したら wrong_kind（#493）。"""
    r = _minimal_valid()
    r["correction_review"]["daily"] = []  # daily は dict であるべき
    viol = ers.check_conformance(r)
    assert any("wrong kind: correction_review.daily" in v for v in viol), viol


def test_toplevel_missing_required_key_is_violation():
    """required top-level キーの欠落は test-time conformance で missing になる（#493）。"""
    r = _minimal_valid()
    del r["growth_report"]
    viol = ers.check_conformance(r)
    assert any("missing: growth_report" in v for v in viol), viol


# ── 7. references/*.md も doc-drift 走査対象（#379-2） ─────────────


def test_references_documented_paths_are_known():
    refs_dir = _REPO / "skills" / "evolve" / "references"
    if not refs_dir.exists():
        pytest.skip("references 不在")
    for md in sorted(refs_dir.rglob("*.md")):
        documented = ers.extract_documented_paths(md.read_text(encoding="utf-8"))
        drift = ers.documented_path_drift(documented)
        assert not drift, (
            f"{md.relative_to(_REPO)} が canonical に無い result path を参照（doc drift）: "
            f"{sorted(drift)}"
        )
