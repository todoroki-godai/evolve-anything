"""advisory 提案の decision lane 配線テスト（#284 / #267 Sprint 1）。

advisory detector（audit の observability section）は「ファイルが変わったか」で accept を
判定する emit→drain lane に1件も載っていなかった（#267 実測前提2）。本テストは接続後の
不変条件を固定する:

  1. emit が advisory 提案を pending に載せる（detector_id / target_path / before_sha 付き）
  2. drain の accept/reject は **optimize_history に入らず** advisory_decisions.jsonl に入る
     （fitness 母集団 skill_quality の均質性を壊さない — evolve_decisions._extract_candidates
     が remediation を除外しているのと同じ理由）
  3. スキル提案（skill_diff / skill_evolve）は従来どおり optimize_history に記録される
  4. 同じ判断を複数回 drain しても記録は1件（read 時 collapse）

すべて LLM-free・決定論。
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import advisory_decision_log as adl  # noqa: E402
import evolve_decisions as ed  # noqa: E402
import optimize_history_store as ohs  # noqa: E402
import rl_common  # noqa: E402


_BROKEN_SKILL = """---
name: broken-skill
description: [unclosed
---

# broken-skill
"""

_FIXED_SKILL = """---
name: broken-skill
description: 直した説明
---

# broken-skill
"""


@pytest.fixture
def project(tmp_path):
    """invalid frontmatter を1件持つ最小 PJ。"""
    skill = tmp_path / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_BROKEN_SKILL, encoding="utf-8")
    return tmp_path


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """marker / queue / optimize_history / DATA_DIR を全て temp に隔離する。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(rl_common, "DATA_DIR", data_dir)
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return data_dir


def _advisory_entries(pending):
    return [p for p in pending if p.get("proposal_type") == "advisory"]


# ─── emit ──────────────────────────────────────────────────────────────────


def test_emit_surfaces_advisory_proposal_with_detector_and_target(isolated, project):
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")

    advisory = _advisory_entries(out["pending"])
    assert len(advisory) == 1
    entry = advisory[0]
    assert entry["detector_id"] == "invalid_frontmatter"
    assert entry["target_path"].endswith("broken-skill/SKILL.md")
    assert entry["before_sha"]
    assert entry["id"].startswith("adv_")
    # advisory は skill_quality 母集団に入らないので fitness_func を持たない。
    assert "fitness_func" not in entry


def test_emit_advisory_id_is_stable_across_runs(isolated, project):
    first = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    second = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")

    ids_first = [p["id"] for p in _advisory_entries(first["pending"])]
    ids_second = [p["id"] for p in _advisory_entries(second["pending"])]
    assert ids_first == ids_second

    # marker も supersede されるので単調増加しない（#279 と同じ不変条件）。
    marker = ed.read_pending_marker("pj")
    assert len(_advisory_entries(marker["pending"])) == 1


def test_emit_skips_advisory_when_project_is_clean(isolated, tmp_path):
    clean = tmp_path / "clean"
    (clean / ".claude" / "skills").mkdir(parents=True)

    out = ed.emit_decisions({}, project_dir=str(clean), dry_run=True, slug="pj")

    assert _advisory_entries(out["pending"]) == []


def test_emit_survives_detector_failure(isolated, project, monkeypatch):
    """detector が壊れても emit 全体は落とさない（advisory は付加価値レーン）。"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(ed, "_collect_advisory_proposals", boom)

    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")

    assert out["pending"] == []


# ─── drain: accept / reject の行き先 ────────────────────────────────────────


def test_advisory_accept_goes_to_advisory_store_not_optimize_history(isolated, project):
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    skill = project / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.write_text(_FIXED_SKILL, encoding="utf-8")  # 人間が適用
    pid = _advisory_entries(out["pending"])[0]["id"]

    summary = ed.drain_pending(slug="pj", accepted={pid})

    assert len(summary["accepted"]) == 1
    records = adl.read_advisory_decisions(slug="pj")
    assert len(records) == 1
    assert records[0]["decision"] == "accept"
    assert records[0]["detector_id"] == "invalid_frontmatter"
    # fitness 母集団は汚さない。
    assert not ohs.history_path("pj").exists()


def test_advisory_reject_records_reason(isolated, project):
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    pid = _advisory_entries(out["pending"])[0]["id"]

    summary = ed.drain_pending(slug="pj", rejected={pid: "後で直す"})

    assert summary["rejected"] == [pid]
    records = adl.read_advisory_decisions(slug="pj")
    assert len(records) == 1
    assert records[0]["decision"] == "reject"
    assert records[0]["reason"] == "後で直す"
    assert not ohs.history_path("pj").exists()


def test_advisory_decision_is_recorded_once_across_repeated_drains(isolated, project):
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    skill = project / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.write_text(_FIXED_SKILL, encoding="utf-8")
    pid = _advisory_entries(out["pending"])[0]["id"]

    ed.drain_pending(slug="pj", accepted={pid})
    # marker は drain で消えるので、同じ pending を再投入して二重 drain を再現する。
    # 対象ファイルは既に修正済みなので再 emit は before_sha が変わり別 id になるが、
    # 実ファイル内容は同じ＝再度 apply されていない（skip）ため、advisory 記録は増えない。
    out2 = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    pid2 = next(iter(_advisory_entries(out2["pending"])), {}).get("id")
    ed.drain_pending(slug="pj", accepted=({pid2} if pid2 else None))

    records = adl.read_advisory_decisions(slug="pj")
    assert len(records) == 1


def test_skill_proposal_still_recorded_in_optimize_history(isolated, tmp_path, project):
    """回帰: advisory 分岐がスキル提案の記録経路を奪わない。"""
    skill = tmp_path / "skills" / "my-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# my-skill\n\n旧手順。\n", encoding="utf-8")
    result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
                ]
            }
        }
    }
    out = ed.emit_decisions(result, project_dir=str(project), dry_run=True, slug="pj")
    skill.write_text("# my-skill\n\n改善された手順。\n", encoding="utf-8")
    skill_pid = next(
        p["id"] for p in out["pending"] if p.get("proposal_type") != "advisory"
    )

    ed.drain_pending(slug="pj", accepted={skill_pid})

    history = ohs.history_path("pj")
    assert history.exists()
    rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines() if line]
    assert [r.get("skill_name") for r in rows] == ["my-skill"]


# ─── SessionStart リマインド ────────────────────────────────────────────────


def test_undrained_applied_surfaces_advisory_entry(isolated, project):
    ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    skill = project / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.write_text(_FIXED_SKILL, encoding="utf-8")

    applied = ed.undrained_applied("pj")

    assert [p["detector_id"] for p in applied] == ["invalid_frontmatter"]


# ─── store 単体 ────────────────────────────────────────────────────────────


def test_summarize_by_detector_counts_decisions(isolated):
    for i, decision in enumerate(["accept", "accept", "reject"]):
        adl.record_advisory_decision(
            slug="pj",
            proposal_id=f"adv_{i}",
            detector_id="testpaths_coverage",
            target_path="pytest.ini",
            decision=decision,
        )
    adl.record_advisory_decision(
        slug="pj",
        proposal_id="adv_x",
        detector_id="invalid_frontmatter",
        target_path="a/SKILL.md",
        decision="accept",
    )

    summary = adl.summarize_by_detector(adl.read_advisory_decisions(slug="pj"))

    assert summary["testpaths_coverage"] == {"accept": 2, "reject": 1}
    assert summary["invalid_frontmatter"] == {"accept": 1, "reject": 0}


def test_read_is_scoped_to_project_slug(isolated):
    adl.record_advisory_decision(
        slug="other-pj",
        proposal_id="adv_1",
        detector_id="invalid_frontmatter",
        target_path="a/SKILL.md",
        decision="accept",
    )

    assert adl.read_advisory_decisions(slug="pj") == []
    assert len(adl.read_advisory_decisions(slug="other-pj")) == 1


def test_later_decision_supersedes_earlier_for_same_proposal(isolated):
    adl.record_advisory_decision(
        slug="pj",
        proposal_id="adv_1",
        detector_id="invalid_frontmatter",
        target_path="a/SKILL.md",
        decision="reject",
        reason="今はやらない",
    )
    adl.record_advisory_decision(
        slug="pj",
        proposal_id="adv_1",
        detector_id="invalid_frontmatter",
        target_path="a/SKILL.md",
        decision="accept",
    )

    records = adl.read_advisory_decisions(slug="pj")

    assert len(records) == 1
    assert records[0]["decision"] == "accept"


def test_reemit_with_changed_evidence_records_single_accept(isolated, project):
    """evidence が変わって advisory ID が変わっても、1回の修正は accept 1件（#290-3）。

    advisory の提案 ID は evidence 込みなので、同じ対象でも evidence（未収集 dir の集合等）が
    変わると別 ID になる。marker supersede が ID 一致だけだと同じ target の pending が
    複数世代 residue し、1回直しただけで全部 accept 判定されて採用率が過大計上される。
    """
    skill = project / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    first = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    # 同じファイルの別の壊れ方 = YAML エラー文が変わる = evidence が変わる = 別 ID
    skill.write_text(_BROKEN_SKILL.replace("[unclosed", "*undefined_alias"), encoding="utf-8")
    second = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    assert (
        _advisory_entries(first["pending"])[0]["id"]
        != _advisory_entries(second["pending"])[0]["id"]
    ), "前提: evidence が変われば advisory ID も変わる"

    marker = ed.read_pending_marker("pj")
    advisory_pending = _advisory_entries(marker["pending"])
    assert len(advisory_pending) == 1
    pid = advisory_pending[0]["id"]

    skill.write_text(_FIXED_SKILL, encoding="utf-8")
    ed.drain_pending(slug="pj", accepted={pid})

    assert len(adl.read_advisory_decisions(slug="pj")) == 1


def test_legacy_store_does_not_override_newer_canonical_decision(isolated, tmp_path, monkeypatch):
    """union read の後段（legacy）にある**古い**判断が canonical の新しい判断を上書きしない（#290-4）。"""
    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    for d in (canonical, legacy):
        d.mkdir()
    monkeypatch.setattr(
        rl_common, "iter_read_data_dirs", lambda: [canonical, legacy]
    )

    def _write(path, decision, recorded_at):
        path.write_text(
            json.dumps(
                {
                    "pj_slug": "pj",
                    "proposal_id": "adv_1",
                    "detector_id": "invalid_frontmatter",
                    "target_path": "a/SKILL.md",
                    "decision": decision,
                    "recorded_at": recorded_at,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    _write(canonical / adl.STORE_NAME, "accept", "2026-07-27T10:00:00+00:00")
    _write(legacy / adl.STORE_NAME, "reject", "2026-01-01T00:00:00+00:00")

    records = adl.read_advisory_decisions(slug="pj")

    assert len(records) == 1
    assert records[0]["decision"] == "accept"


def test_store_is_declared_active_in_registry():
    import store_registry

    decl = store_registry.declaration_for(adl.STORE_NAME)
    assert decl is not None
    assert decl.status == "active"
    assert decl.writer_locus == "batch"
