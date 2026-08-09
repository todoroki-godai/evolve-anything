"""store_registry の 4 分類（classification）契約テスト（#379 Step 3）。

決定論・LLM 非依存。全 store を trusted raw event / workflow state / derived cache /
dead の 4 分類に機械可読 SoT 化した契約を検証する。classification は status
（write barrier の生死・ADR-049）とは独立の軸: 現状の運用フェーズ
（raw_event/workflow_state/derived_cache）と将来の削除対象（dead）を分離して表す。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import shrink_freeze  # noqa: E402
import store_registry  # noqa: E402

_VALID_CLASSIFICATIONS = {"raw_event", "workflow_state", "derived_cache", "dead"}

# classification="dead" だが status はまだ "active" のまま残す既知の例外
# （#379 Step 3 棚卸しレビューで発見）。judge_audit_verdicts.jsonl の実 writer
# （judge_audit/harness.py の --run）は store_write barrier を経由する live な
# オプトイン CLI 機能で、scripts/lib/tests/test_judge_audit.py が
# run_audit(run=True) を通じて実際に barrier を通過する形で回帰検証している。
# status=dead に降格すると reject 既定の write barrier が StoreWriteError を送出し、
# CLI 機能そのものと既存テスト 6 件超が同時に壊れる。「機能を止めてでも退役する」か
# 「barrier だけ迂回させる」かは製品判断であり Step 3 のスコープ外のため、
# classification のみ dead 化して status は保留する（実装報告で確認事項として明記）。
#
# 解消条件（#379 Step 3 レビュー）: Step 4 で judge_audit harness（judge_audit/harness.py
# の --run CLI 機能）を削除し、上記テストごと退役するタイミングで status=dead に降格し、
# 本例外集合を空にする。新規例外の無断追加は
# test_status_dead_exempt_has_no_undocumented_additions が検出する。
_STATUS_DEAD_EXEMPT = {"judge_audit_verdicts.jsonl"}

_RAW_EVENT = {
    "corrections.jsonl",
    "usage.jsonl",
    "usage-registry.jsonl",
    "sessions.jsonl",
    "errors.jsonl",
    "workflows.jsonl",
    "skill_activations.jsonl",
    "subagents.jsonl",
    "utterances.db",
    "verbosity_candidates.jsonl",
}

_WORKFLOW_STATE = {
    "weak_signals.jsonl",
    "bootstrap_done-<slug>.marker",
    "correction_review_seen.jsonl",
    "remediation_suppression/<slug>.jsonl",
    "remediation_surfaced/<slug>.json",
    "icebox_verdict_seen.jsonl",
    "evolve-queue-state.jsonl",
    "false_positives.jsonl",
    "correction_idioms.jsonl",
    "discover-suppression.jsonl",
    "belief_blocks.jsonl",
    "audit-history.jsonl",
    "advisory_decisions.jsonl",
    "correction_judged.jsonl",
    "memory_transition_checks.jsonl",
}

_DERIVED_CACHE = {
    "sessions.db",
    "token_usage.db",
    "subagent_traces.jsonl",
    "reward_ema.jsonl",
    "verbosity_verdicts.jsonl",
    "evolution_memory.jsonl",
    "quality-baselines.jsonl",
    "episodic.db",
}

_DEAD = {
    "quality-scores.jsonl",
    "growth-journal.jsonl",
    "judge_audit_verdicts.jsonl",
}


def test_all_declarations_have_a_classification() -> None:
    for d in store_registry.declarations():
        assert d.classification is not None, d.name
        assert d.classification in _VALID_CLASSIFICATIONS, f"{d.name}: {d.classification!r}"


def test_status_dead_implies_classification_dead() -> None:
    """write barrier が既に dead（write/read しない）なら classification も dead。"""
    for d in store_registry.declarations():
        if d.status == "dead":
            assert d.classification == "dead", d.name


def test_classification_dead_implies_status_dead_except_documented_exemptions() -> None:
    """classification=dead は通常 status=dead を伴う。例外は明示リストのみ許容する。"""
    for d in store_registry.declarations():
        if d.classification == "dead" and d.name not in _STATUS_DEAD_EXEMPT:
            assert d.status == "dead", d.name


# golden: _STATUS_DEAD_EXEMPT の意図された内容（無断拡大の機械ガード・#379 Step 3 レビュー）。
_STATUS_DEAD_EXEMPT_GOLDEN = frozenset({"judge_audit_verdicts.jsonl"})


def test_status_dead_exempt_has_no_undocumented_additions() -> None:
    """_STATUS_DEAD_EXEMPT が golden 集合から乖離していないこと。

    新しい dead-but-status-active 例外を追加する場合は、このテストの golden 集合と
    上の解消条件コメント（Step 4 で judge_audit harness を削除するタイミングで空にする）を
    同時に更新すること。片方だけ変えて通ることを防ぎ、無言の例外拡大を機械的に禁止する。
    """
    assert _STATUS_DEAD_EXEMPT == _STATUS_DEAD_EXEMPT_GOLDEN, (
        f"_STATUS_DEAD_EXEMPT が golden {_STATUS_DEAD_EXEMPT_GOLDEN} から乖離しています: "
        f"{_STATUS_DEAD_EXEMPT}。新規追加は理由と解消条件をコメントに明記のうえ両方更新すること。"
    )


def test_classification_golden_counts_and_names() -> None:
    """4 分類の件数（10/15/8/3）と名前リストが棚卸し表と一致する（golden）。"""
    by_classification: dict[str, set[str]] = {}
    for d in store_registry.declarations():
        by_classification.setdefault(d.classification, set()).add(d.name)

    assert by_classification.get("raw_event", set()) == _RAW_EVENT
    assert by_classification.get("workflow_state", set()) == _WORKFLOW_STATE
    assert by_classification.get("derived_cache", set()) == _DERIVED_CACHE
    assert by_classification.get("dead", set()) == _DEAD

    assert len(_RAW_EVENT) == 10
    assert len(_WORKFLOW_STATE) == 15
    assert len(_DERIVED_CACHE) == 8
    assert len(_DEAD) == 3


def test_classification_categories_partition_all_declarations() -> None:
    """4 分類は全宣言を過不足なく分割する（重複・欠落なし）。"""
    all_names = {d.name for d in store_registry.declarations()}
    union = _RAW_EVENT | _WORKFLOW_STATE | _DERIVED_CACHE | _DEAD
    assert union == all_names
    assert len(union) == len(_RAW_EVENT) + len(_WORKFLOW_STATE) + len(_DERIVED_CACHE) + len(_DEAD)


def test_frozen_stores_snapshot_unchanged_by_classification() -> None:
    """classification 追加は既存ストアの名前集合を増減させない（#379 Step 1 不変条件）。"""
    live = set(store_registry.declared_store_names())
    shrink_freeze.assert_no_new_keys(live, shrink_freeze.FROZEN_STORES, "store")
    assert live == set(shrink_freeze.FROZEN_STORES)


def test_declarations_by_classification_helper() -> None:
    dead = store_registry.declarations_by_classification("dead")
    assert {d.name for d in dead} == _DEAD
    for d in dead:
        assert d.classification == "dead"
