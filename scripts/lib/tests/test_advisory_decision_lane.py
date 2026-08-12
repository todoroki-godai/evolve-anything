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
from datetime import datetime, timezone
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


def test_ingest_dry_run_does_not_write_surfaced_or_deferred(isolated, project):
    """dry_run=True では surfaced/deferred も含め advisory_decisions.jsonl に一切書かない
    （#308 / ADR-041: 書込は apply 境界のみ）。"""
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")

    ed.ingest_decisions("pj", dry_run=True, pending=out["pending"])

    assert adl.read_advisory_decisions(slug="pj") == []


def test_advisory_accept_goes_to_advisory_store_not_optimize_history(isolated, project):
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    skill = project / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.write_text(_FIXED_SKILL, encoding="utf-8")  # 人間が適用
    pid = _advisory_entries(out["pending"])[0]["id"]

    summary = ed.drain_pending(slug="pj", accepted={pid})

    assert len(summary["accepted"]) == 1
    records = {r["decision"]: r for r in adl.read_advisory_decisions(slug="pj")}
    # accept（terminal）と surfaced（fact）は独立に記録される（#267 Sprint 1）。
    assert set(records) == {"accept", "surfaced"}
    assert records["accept"]["detector_id"] == "invalid_frontmatter"
    # fitness 母集団は汚さない。
    assert not ohs.history_path("pj").exists()


def test_advisory_reject_records_reason(isolated, project):
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    pid = _advisory_entries(out["pending"])[0]["id"]

    summary = ed.drain_pending(slug="pj", rejected={pid: "後で直す"})

    assert summary["rejected"] == [pid]
    records = {r["decision"]: r for r in adl.read_advisory_decisions(slug="pj")}
    assert set(records) == {"reject", "surfaced"}
    assert records["reject"]["reason"] == "後で直す"
    assert not ohs.history_path("pj").exists()


def test_drain_records_surfaced_and_deferred_for_unresolved_proposal(isolated, project):
    """人間が未着手（未修正・未却下）なら deferred として記録される（#267 Sprint 1）。"""
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug="pj")
    pid = _advisory_entries(out["pending"])[0]["id"]

    summary = ed.drain_pending(slug="pj")

    assert summary["deferred"] == [pid]
    records = {r["decision"]: r for r in adl.read_advisory_decisions(slug="pj")}
    assert set(records) == {"surfaced", "deferred"}
    assert records["deferred"]["detector_id"] == "invalid_frontmatter"


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
    assert {r["decision"] for r in records} == {"accept", "surfaced"}
    assert len(records) == 2


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


# ─── audit reader への到達確認（#267 Sprint 1） ─────────────────────────────


def test_full_pipeline_reaches_audit_section_with_surfaced_and_accept(isolated, project):
    """emit→drain（実 pipeline）の結果が audit の Advisory Decisions section まで届く。

    writer だけ見て「動いている」と誤読しない（#339 と同型の罠）ため、adl の直接呼び出し
    でなく実際の emit_decisions/drain_pending を経由した記録が reader まで到達することを
    固定する。
    """
    from audit.sections_advisory_decisions import build_advisory_decisions_section

    # slug は section 側の resolve_pj_slug と同じ値を使う（emit/drain 用に固定 "pj" を
    # 使うと section 側の slug と不一致になり読めない）。
    slug = ed.resolve_slug(project)
    out = ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug=slug)
    skill = project / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.write_text(_FIXED_SKILL, encoding="utf-8")
    # accept は「明示 accept + 適用実績」の AND（#376 AC1）。ファイル修正だけでは
    # accept にならないので、明示 accept の証跡も渡す。
    pid = _advisory_entries(out["pending"])[0]["id"]
    ed.drain_pending(slug=slug, accepted={pid})

    lines = build_advisory_decisions_section(project)

    assert "| invalid_frontmatter | 1 | 1 | 0 | 0 | 0 | 100% |" in lines


def test_full_pipeline_reaches_audit_section_with_deferred(isolated, project):
    """未着手（deferred）も accept と同様に reader まで届く。"""
    from audit.sections_advisory_decisions import build_advisory_decisions_section

    slug = ed.resolve_slug(project)
    ed.emit_decisions({}, project_dir=str(project), dry_run=True, slug=slug)
    ed.drain_pending(slug=slug)

    lines = build_advisory_decisions_section(project)

    assert "| invalid_frontmatter | 1 | 0 | 0 | 1 | 1 | 0% |" in lines


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

    assert summary["testpaths_coverage"] == {
        "surfaced": 0, "accept": 2, "reject": 1, "deferred": 0,
        "accept_in_cohort": 0, "legacy_accept": 2,
        "reject_in_cohort": 0, "legacy_reject": 1, "open": 0,
    }
    assert summary["invalid_frontmatter"] == {
        "surfaced": 0, "accept": 1, "reject": 0, "deferred": 0,
        "accept_in_cohort": 0, "legacy_accept": 1,
        "reject_in_cohort": 0, "legacy_reject": 0, "open": 0,
    }


def test_record_period_returns_earliest_and_latest_dates(isolated):
    """``record_period``（#381 D・round3 で public 昇格）は最古／最新 recorded_at を返す。"""
    adl.record_advisory_decision(
        slug="pj", proposal_id="adv_1", detector_id="invalid_frontmatter",
        target_path="pytest.ini", decision="surfaced",
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    adl.record_advisory_decision(
        slug="pj", proposal_id="adv_1", detector_id="invalid_frontmatter",
        target_path="pytest.ini", decision="accept",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    period = adl.record_period(adl.read_advisory_decisions(slug="pj"))

    assert period == {"earliest": "2026-05-01", "latest": "2026-08-12", "days": 103}


def test_record_period_returns_none_when_no_records(isolated):
    assert adl.record_period([]) is None


def test_record_period_ignores_records_without_recorded_at(isolated):
    """``recorded_at`` 欠損レコードは無視する（例外を出さない・防御的）。"""
    assert adl.record_period([{"decision": "surfaced", "proposal_id": "adv_1"}]) is None


def test_summarize_open_is_surfaced_without_terminal(isolated):
    """``open``（未判断）= surfaced 記録あり ∧ accept/reject 記録なし（proposal_id 単位・#381 B）。"""
    for pid in ("adv_decided", "adv_open"):
        adl.record_advisory_decision(
            slug="pj", proposal_id=pid, detector_id="testpaths_coverage",
            target_path="pytest.ini", decision="surfaced",
        )
    adl.record_advisory_decision(
        slug="pj", proposal_id="adv_decided", detector_id="testpaths_coverage",
        target_path="pytest.ini", decision="accept",
    )

    summary = adl.summarize_by_detector(adl.read_advisory_decisions(slug="pj"))

    assert summary["testpaths_coverage"]["open"] == 1


def test_surfaced_and_terminal_decision_coexist_for_same_proposal(isolated):
    """surfaced/deferred は accept/reject と別バケツで collapse される（#267 Sprint 1）。

    同じ提案が「deferred のまま後日 accept された」場合、両方の事実が残ることで
    「一度は見送られたが後で採用された」という経緯が読み取れる。
    """
    for decision in ("surfaced", "deferred", "accept"):
        adl.record_advisory_decision(
            slug="pj",
            proposal_id="adv_1",
            detector_id="invalid_frontmatter",
            target_path="a/SKILL.md",
            decision=decision,
        )

    records = adl.read_advisory_decisions(slug="pj")

    assert {r["decision"] for r in records} == {"surfaced", "deferred", "accept"}
    assert len(records) == 3


def test_repeated_surfaced_events_collapse_to_one(isolated):
    """同じ提案を複数回 drain しても surfaced は1件に畳む（水増ししない）。"""
    for _ in range(3):
        adl.record_advisory_decision(
            slug="pj",
            proposal_id="adv_1",
            detector_id="invalid_frontmatter",
            target_path="a/SKILL.md",
            decision="surfaced",
        )

    records = adl.read_advisory_decisions(slug="pj")

    assert len(records) == 1
    assert records[0]["decision"] == "surfaced"


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
    """evidence が変わって advisory ID が変わっても、1回の修正は accept 1件（+ surfaced 1件）（#290-3）。

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

    records = adl.read_advisory_decisions(slug="pj")
    assert {r["decision"] for r in records} == {"accept", "surfaced"}
    assert len(records) == 2


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
