"""非 dry-run の outcome E2E（#400 follow-up / learning_dryrun_verification_blind_spot）。

**dry-run 出力でなく『正準 store の差分（outcome）』を assert する**のが本テストの肝。
evolve_pj_harness で隔離 PJ を組み、emit→**apply**→ingest→fitness の実サイクルと
reconcile / observability の wiring を、apply 境界をまたいで outcome で検証する。

これが緑である限り「dry-run では緑だが実 evolve で効果が出ない」（#400 バグ#1 の症状）は
構造的に再発しない。assessment の LLM 判定や apply customization は LLM を使うため対象外
（RL_ALLOW_LLM_IN_TESTS gated の integration 送り）。ここは決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS))
sys.path.insert(0, str(_TESTS.parent / "lib"))

import evolve_decisions as ed  # noqa: E402
import evolve_reconcile as er  # noqa: E402
from evolve_pj_harness import (  # noqa: E402
    apply_skill_change,
    build_evolve_test_pj,
    make_skill_evolve_result,
)

_FE = _TESTS.parent.parent / "skills" / "evolve-fitness" / "scripts"
sys.path.insert(0, str(_FE))
import fitness_evolution as fe  # noqa: E402


_IMPROVED = "---\nname: skill-used\ndescription: improved trigger\n---\n\n改善された手順を踏む。\n"


def test_nondryrun_apply_increments_fitness_population(tmp_path, monkeypatch):
    """dry-run 分析 → apply → ingest で fitness 母集団が実際に +1 する（#400 バグ#1 outcome）。

    旧コードはこの outcome を構造的に達成できなかった（emit が dry-run でキューを書かず
    Step 7.8 も記録スキップ → optimize_history 永久に空 → fitness 0/30 固定）。
    """
    pj = build_evolve_test_pj(tmp_path, monkeypatch)
    result = make_skill_evolve_result(pj, high=["skill-used"])

    # 母集団は 0 から始まる
    assert fe.load_history(history_file=pj.history_path()) == []

    # 1) dry-run emit（キューは書かない）→ pending を得る
    out = ed.emit_decisions(result, dry_run=True, slug=pj.slug)
    assert ed.read_queue(pj.slug) == []  # dry-run はストアに何も書かない

    # 2) assistant が apply（apply 境界 — ここを越えないと効果は出ない）
    apply_skill_change(pj, "skill-used", _IMPROVED)

    # 3) Step 7.8 ingest（result の pending を直接渡す＝キュー不在でも動く）
    summary = ed.ingest_decisions(pj.slug, dry_run=False, pending=out["pending"])
    assert len(summary["accepted"]) == 1

    # 4) outcome: fitness が同じ正準 store を読んで +1 を観測する
    history = fe.load_history(history_file=pj.history_path())
    assert len(history) == 1
    assert history[0]["human_accepted"] is True
    assert fe.run_fitness_evolution(history=history)["data_count"] == 1  # 0 から動いた


def test_nondryrun_reconcile_excludes_archive_overlap(tmp_path, monkeypatch):
    """archive 候補かつ skill_evolve 提案の重複を reconcile が抑制し、emit 母集団からも外れる（#400 バグ#2 outcome）。"""
    pj = build_evolve_test_pj(tmp_path, monkeypatch)
    # skill-archive を high 提案 かつ prune archive 候補に重複させる（矛盾状態）
    result = make_skill_evolve_result(
        pj, high=["skill-used", "skill-archive"], archive=["skill-archive"]
    )

    summary = er.reconcile_skill_evolve_archive(result)
    assert summary["suppressed"] == ["skill-archive"]
    # high カウントも降格を反映
    assert result["phases"]["skill_evolve"]["high_suitability"] == 1

    # emit は suppress 後の assessments を見るので skill-archive を拾わない（母集団に矛盾候補を入れない）
    out = ed.emit_decisions(result, dry_run=True, slug=pj.slug)
    names = {p["skill_name"] for p in out["pending"]}
    assert "skill-archive" not in names
    assert "skill-used" in names


def test_nondryrun_batch_skip_surfaced_in_observability(tmp_path, monkeypatch):
    """batch_skip 件数が observability に昇格される（#400 バグ#6 outcome / silence != evaluated）。"""
    pj = build_evolve_test_pj(tmp_path, monkeypatch)
    result = make_skill_evolve_result(pj, high=["skill-used"], batch_skip_count=4)
    lines = er.build_remediation_batch_skip_observability(result)
    assert lines is not None
    assert "4 件" in lines[0]
    # 0件でも surface する（沈黙にしない）
    result0 = make_skill_evolve_result(pj, high=["skill-used"], batch_skip_count=0)
    assert er.build_remediation_batch_skip_observability(result0) == [
        "✓ remediation batch_skip: 0 件（まとめスキップ対象なし）"
    ]


def test_nondryrun_reconcile_then_batch_skip_count_stays_consistent(tmp_path, monkeypatch):
    """reconcile（4.2）→ batch_skip observability（4.3）を本流順で通しても count が壊れない。

    fixture が top-level int == len(classified list) を満たさないと、reconcile の
    remediation[bucket]=len(kept) が batch_skip を 0 に潰し observability が誤った
    「0 件」を出す（learning_synthetic_fixture_false_confidence）。本テストは harness の
    契約 fidelity を outcome で固定する回帰ガード。
    """
    pj = build_evolve_test_pj(tmp_path, monkeypatch)
    # archive 重複あり（reconcile が発火する状態）かつ batch_skip 3 件
    result = make_skill_evolve_result(
        pj, high=["skill-used", "skill-archive"], archive=["skill-archive"], batch_skip_count=3
    )
    er.reconcile_skill_evolve_archive(result)  # 本流 Phase 4.2
    # batch_skip の中身は skill_evolve_candidate ではないので件数は不変のはず
    lines = er.build_remediation_batch_skip_observability(result)  # 本流 Phase 4.3
    assert lines is not None
    assert "3 件" in lines[0]


def test_nondryrun_pure_preview_records_nothing(tmp_path, monkeypatch):
    """何も apply しなければ母集団は増えない（self-correcting・副作用チェック）。"""
    pj = build_evolve_test_pj(tmp_path, monkeypatch)
    result = make_skill_evolve_result(pj, high=["skill-used"])
    out = ed.emit_decisions(result, dry_run=True, slug=pj.slug)

    # apply しない → ingest しても全件 skip、store は空のまま
    summary = ed.ingest_decisions(pj.slug, dry_run=False, pending=out["pending"])
    assert summary["accepted"] == []
    assert summary["skipped"]  # 未変更は skip
    assert fe.load_history(history_file=pj.history_path()) == []  # 母集団は増えない


def test_nondryrun_reject_records_negative(tmp_path, monkeypatch):
    """明示却下は human_accepted=False で母集団に入る（accept/reject 両方が貯まる）。"""
    pj = build_evolve_test_pj(tmp_path, monkeypatch)
    result = make_skill_evolve_result(pj, high=["skill-used"])
    out = ed.emit_decisions(result, dry_run=True, slug=pj.slug)
    pid = out["pending"][0]["id"]
    # 未 apply + 明示却下
    summary = ed.ingest_decisions(
        pj.slug, dry_run=False, pending=out["pending"], rejected={pid: "ドメイン不一致"}
    )
    assert summary["rejected"] == [pid]
    history = fe.load_history(history_file=pj.history_path())
    assert len(history) == 1
    assert history[0]["human_accepted"] is False
