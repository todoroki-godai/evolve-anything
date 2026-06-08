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
    not (_REPO / "skills" / "evolve" / "scripts" / "evolve.py").exists(),
    reason="evolve.py 不在",
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
    canonical = ers.canonical_paths()
    unknown = documented - canonical
    assert not unknown, (
        f"SKILL.md が canonical に無い result path を参照（doc drift）: {sorted(unknown)}。"
        f"evolve_result_schema.CANONICAL を更新するか SKILL.md を修正すること"
    )


def test_extract_strips_result_prefix():
    paths = ers.extract_documented_paths("`result.phases.skill_evolve.batch_guard_trigger`")
    assert "phases.skill_evolve.batch_guard_trigger" in paths
