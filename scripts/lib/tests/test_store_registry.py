"""store_registry（ストア新設の事前契約ゲート宣言）のテスト（#434）。

決定論・LLM 非依存。宣言 SoT 自身の整合性と、実プラグインツリーの全 hook writer が
宣言バックフィル済みであること（issue の Success Criteria）を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import orphan_store  # noqa: E402
import store_registry  # noqa: E402
from store_registry import StoreDeclaration  # noqa: E402


# --- 宣言 SoT 自身の整合性 ---------------------------------------------------

def test_real_declarations_are_internally_consistent() -> None:
    """同梱の宣言 SoT は validate_declarations を通過する（retention 不整合なし・重複なし）。"""
    assert store_registry.validate_declarations() == []


def test_declared_names_are_sorted_and_unique() -> None:
    names = store_registry.declared_store_names()
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_declaration_for_returns_entry_or_none() -> None:
    assert store_registry.declaration_for("corrections.jsonl") is not None
    assert store_registry.declaration_for("no_such_store.jsonl") is None


# --- validate_declarations のルール ------------------------------------------

def test_ttl_requires_ttl_days() -> None:
    bad = [
        StoreDeclaration(
            name="x.jsonl", writer="w", reader="r", retention="ttl", classification="workflow_state"
        )
    ]
    problems = store_registry.validate_declarations(bad)
    assert any("ttl_days" in p for p in problems)


def test_compaction_requires_condition() -> None:
    bad = [
        StoreDeclaration(
            name="x.jsonl",
            writer="w",
            reader="r",
            retention="compaction",
            classification="derived_cache",
        )
    ]
    problems = store_registry.validate_declarations(bad)
    assert any("compaction" in p for p in problems)


def test_permanent_rejects_ttl_or_compaction() -> None:
    bad = [
        StoreDeclaration(
            name="x.jsonl",
            writer="w",
            reader="r",
            retention="permanent",
            classification="raw_event",
            ttl_days=7,
        )
    ]
    problems = store_registry.validate_declarations(bad)
    assert any("不整合" in p for p in problems)


def test_duplicate_names_flagged() -> None:
    dup = [
        StoreDeclaration(
            name="x.jsonl", writer="w", reader="r", retention="permanent", classification="raw_event"
        ),
        StoreDeclaration(
            name="x.jsonl", writer="w2", reader="r2", retention="permanent", classification="raw_event"
        ),
    ]
    problems = store_registry.validate_declarations(dup)
    assert any("重複" in p for p in problems)


def test_valid_ttl_and_compaction_pass() -> None:
    good = [
        StoreDeclaration(
            name="a.jsonl",
            writer="w",
            reader="r",
            retention="ttl",
            classification="workflow_state",
            ttl_days=14,
        ),
        StoreDeclaration(
            name="b.jsonl",
            writer="w",
            reader="r",
            retention="compaction",
            classification="derived_cache",
            compaction="1MB でローテーション",
        ),
    ]
    assert store_registry.validate_declarations(good) == []


# --- Success Criteria: 既存全ストアの宣言バックフィル完了 ----------------------

def test_all_live_hook_writers_are_declared() -> None:
    """実プラグインツリーで登録 hook が書く全 jsonl ストアが store_registry に宣言済み。

    宣言バックフィルの完了を保証する回帰テスト（#434 Success Criteria）。
    将来、宣言を足さずに新 writer hook を追加すると、このテストが落ちて気付ける。
    """
    writers = set(orphan_store.find_store_writers())
    declared = set(store_registry.declared_store_names())
    missing = sorted(writers - declared)
    assert missing == [], f"宣言なしの hook writer ストア: {missing}"


def test_current_orphan_disposition_is_declared() -> None:
    """orphan_store が現在挙げる orphan（reader 0）は disposition 宣言を持つ（#434）。"""
    report = orphan_store.detect_orphan_stores()
    for name in report.orphans:
        decl = store_registry.declaration_for(name)
        assert decl is not None, f"orphan {name} が未宣言"
        assert decl.disposition is not None, f"orphan {name} に disposition がない"


# --- .db ストア対応（#430）---------------------------------------------------

def test_utterances_db_declared_as_permanent_db() -> None:
    """utterances.db が kind='db' / retention='permanent' で宣言されている（#430）。"""
    decl = store_registry.declaration_for("utterances.db")
    assert decl is not None
    assert decl.kind == "db"
    assert decl.retention == "permanent"


def test_db_stores_excluded_from_hook_writer_backfill() -> None:
    """db ストアは hook-writer 突合の母集団でない（writer が batch ingest）。

    declarations_by_kind('jsonl') に db ストアが混ざらないことを保証する。
    """
    jsonl_names = {d.name for d in store_registry.declarations_by_kind("jsonl")}
    db_names = {d.name for d in store_registry.declarations_by_kind("db")}
    assert "utterances.db" in db_names
    assert "utterances.db" not in jsonl_names


def test_db_declaration_does_not_appear_as_stale_drift() -> None:
    """db ストア宣言が contract-drift の stale に誤検知されない（#430）。"""
    drift = orphan_store.detect_store_contract_drift()
    assert "utterances.db" not in drift.stale


# --- weak_signals.jsonl の TTL 宣言（#442）-----------------------------------

def test_weak_signals_declared_with_ttl_45() -> None:
    """weak_signals.jsonl が retention='ttl' / ttl_days=45 で宣言されている（#442）。"""
    decl = store_registry.declaration_for("weak_signals.jsonl")
    assert decl is not None
    assert decl.retention == "ttl"
    assert decl.ttl_days == 45


# --- reward_ema.jsonl の宣言（#64・MAA バッチ跨ぎ EMA）-----------------------

def test_reward_ema_declared_as_active_batch_permanent() -> None:
    """reward_ema.jsonl が active / batch-writer / permanent で宣言されている（#64）。

    evolve --drain の apply 境界が persist_reward_ema_batch で書く新ストア。宣言なしで
    書くと store_write が StoreWriteError で reject し、orphan_store も undeclared として
    surface する（#434 / ADR-049）。
    """
    decl = store_registry.declaration_for("reward_ema.jsonl")
    assert decl is not None
    assert decl.status == "active"
    assert decl.retention == "permanent"
    assert decl.writer_locus == "batch"


def test_reward_ema_write_not_rejected_by_barrier() -> None:
    """active 宣言なので store_write の runtime guard が write を弾かない（#64）。"""
    assert store_registry.is_active_store("reward_ema.jsonl") is True


def test_reward_ema_not_stale_drift() -> None:
    """batch-writer 宣言なので hook-writer 突合の stale に誤検知されない（#64）。"""
    assert "reward_ema.jsonl" in store_registry.stale_exempt_names()


# --- subagent_traces.jsonl の宣言（#38・subagent 内部軌跡）-------------------

def test_subagent_traces_declared_as_active_batch_permanent() -> None:
    """subagent_traces.jsonl が active / batch-writer / permanent で宣言されている（#38）。

    evolve batch の apply 境界が ingest_all_projects で書く新ストア。宣言なしで書くと
    store_write が StoreWriteError で reject し、orphan_store も undeclared として surface
    する（#434 / ADR-049）。
    """
    decl = store_registry.declaration_for("subagent_traces.jsonl")
    assert decl is not None
    assert decl.status == "active"
    assert decl.retention == "permanent"
    assert decl.writer_locus == "batch"


def test_subagent_traces_write_not_rejected_by_barrier() -> None:
    """active 宣言なので store_write の runtime guard が write を弾かない（#38）。"""
    assert store_registry.is_active_store("subagent_traces.jsonl") is True


def test_subagent_traces_not_stale_drift() -> None:
    """batch-writer 宣言なので hook-writer 突合の stale に誤検知されない（#38）。"""
    assert "subagent_traces.jsonl" in store_registry.stale_exempt_names()


# --- correction_review_seen.jsonl の宣言（#446）------------------------------

def test_correction_review_seen_declared_as_batch_permanent() -> None:
    """correction_review_seen.jsonl が batch-writer / permanent で宣言されている（#446）。

    既読集合は evolve batch（daily_review）が書く新ストア。宣言なしで書くと
    orphan_store が undeclared として surface する（#434）。
    """
    decl = store_registry.declaration_for("correction_review_seen.jsonl")
    assert decl is not None
    assert decl.retention == "permanent"
    assert decl.writer_locus == "batch"


def test_correction_review_seen_not_stale_drift() -> None:
    """batch-writer 宣言なので hook-writer 突合の stale に誤検知されない（#446）。"""
    assert "correction_review_seen.jsonl" in store_registry.stale_exempt_names()


# --- remediation_suppression/<slug>.jsonl の宣言（#477）----------------------

def test_remediation_suppression_declared_as_batch_ttl_45() -> None:
    """remediation_suppression/<slug>.jsonl が batch-writer / ttl=45 で宣言されている（#477）。

    remediation 個別承認で却下された提案の suppression ledger。evolve batch が書く
    新ストアで、宣言なしで書くと orphan_store が undeclared として surface する（#434）。
    """
    decl = store_registry.declaration_for("remediation_suppression/<slug>.jsonl")
    assert decl is not None
    assert decl.retention == "ttl"
    assert decl.ttl_days == 45
    assert decl.writer_locus == "batch"


def test_remediation_suppression_not_stale_drift() -> None:
    """batch-writer 宣言なので hook-writer 突合の stale に誤検知されない（#477）。"""
    assert "remediation_suppression/<slug>.jsonl" in store_registry.stale_exempt_names()


# --- optimize_history/<slug>.jsonl の宣言（#475 §12 決定4）--------------------


def test_optimize_history_declared_as_active_batch_permanent_raw_event() -> None:
    """optimize_history/<slug>.jsonl が active / batch-writer / permanent / raw_event で
    宣言されている（#475 §12 決定4）。既存 4 writer（optimize.py・run_loop.py・
    fitness_evolution.py・evolve_revert/_apply.py）が採用/revert イベントを書く既存
    ストアの登録漏れ追認であり、宣言なしで書くと orphan_store が undeclared として
    surface する（#434）。
    """
    decl = store_registry.declaration_for("optimize_history/<slug>.jsonl")
    assert decl is not None
    assert decl.status == "active"
    assert decl.retention == "permanent"
    assert decl.writer_locus == "batch"
    assert decl.classification == "raw_event"
    assert decl.writer, "writer 未記述"
    assert decl.reader, "reader 未記述"


def test_optimize_history_write_not_rejected_by_barrier() -> None:
    """active 宣言なので store_write の runtime guard が write を弾かない（#475）。"""
    assert store_registry.is_active_store("optimize_history/<slug>.jsonl") is True


def test_optimize_history_not_stale_drift() -> None:
    """batch-writer 宣言なので hook-writer 突合の stale に誤検知されない（#475）。"""
    assert "optimize_history/<slug>.jsonl" in store_registry.stale_exempt_names()


def test_optimize_history_included_in_active_store_names() -> None:
    assert "optimize_history/<slug>.jsonl" in store_registry.active_store_names()


# --- status フィールド（write barrier・ADR-049 / #55）------------------------

def test_status_defaults_to_active() -> None:
    """status 未指定の宣言は active 既定（write barrier の write 許可対象）。"""
    decl = store_registry.declaration_for("corrections.jsonl")
    assert decl.status == "active"


# #121: 未登録 legacy ストア11件を registry 宣言（status=legacy）。writer/reader を実 grep
# で確認済み。
# #379 Step 3: quality-scores.jsonl / growth-journal.jsonl は classification=dead に伴い
# status も legacy→dead へ降格（writer が store_write barrier 非経由の直接 open() のため
# 降格しても実行時に壊れないことを確認済み）。judge_audit_verdicts.jsonl は当時
# classification=dead だが status=active のまま据え置く例外だったが、#379 Step 4 で
# judge_audit harness ごと宣言削除され例外自体が解消した
# （test_store_classification.py の `_STATUS_DEAD_EXEMPT` 参照）。quality-scores.jsonl も
# growth-journal.jsonl も #379 Step 4 で writer（quality_engine.record_quality_score /
# growth_journal.emit_crystallization 他）ごと宣言削除された。dead 集合は恒久的に空になり得る。
_LEGACY_STORES_121 = [
    "audit-history.jsonl",       # writer live: audit orchestrator _record_audit_completion
    "belief_blocks.jsonl",       # writer live: auto_memory_broker _record_belief_block
    "discover-suppression.jsonl",  # writer live: discover/suppression の record 群
    "episodic.db",               # writer live: reflect の promote_to_episodic→insert_event
    "evolution_memory.jsonl",    # writer live: genetic-prompt-optimizer optimize.save_winner
    "quality-baselines.jsonl",   # writer live: quality_monitor save_baselines/append_record
    "sessions.db",               # writer live: session_store.ingest (batch)
    "token_usage.db",            # writer live: token_usage_store の bulk INSERT
]
_DEAD_STORES_121: list = []


def test_legacy_and_dead_stores_declared_121() -> None:
    """#121/#379 Step 3/4: legacy 8 件 + dead 0 件が正しい status で宣言されている。

    旧 test_all_real_declarations_are_active（全 active 前提）を #121 の段階導入に更新。
    active は既存のまま、legacy/dead は #121/#379 で新規宣言・降格した既知集合のみが持つ。
    """
    by_status = {}
    for d in store_registry.declarations():
        by_status.setdefault(d.status, set()).add(d.name)
    assert set(_LEGACY_STORES_121) <= by_status.get("legacy", set())
    assert set(_DEAD_STORES_121) <= by_status.get("dead", set())
    # legacy/dead は #121/#379 で導入・降格した既知集合に限定（意図しない降格を検出）。
    assert by_status.get("legacy", set()) == set(_LEGACY_STORES_121)
    assert by_status.get("dead", set()) == set(_DEAD_STORES_121)


def test_legacy_dead_stores_not_active_121() -> None:
    """#121: legacy/dead ストアは active でない（write barrier が store_write を弾く）。

    これらは直接 writer（open/INSERT・store_write 非経由）で書くため、非 active でも
    実行時には壊れない。active でないことで write barrier の active-only 集合から外れる。
    """
    for name in _LEGACY_STORES_121 + _DEAD_STORES_121:
        assert store_registry.is_active_store(name) is False, name
        assert name not in store_registry.active_store_names(), name


_PR_E_STORES = [
    "evolve-state.json",
    "remediation-outcomes.jsonl",
    "fleet-config.json",
    "agent-brushup-state.json",
    "skill-evolve-denylist.json",
    "pj_slug_cache.json",
    "skill-evolve-cache.json",
    # round2（#399 codex Must 2）: read専用派生物 4件の宣言追加。
    "evolve-queue.json",
    "icebox-status.json",
    "icebox-verdicts.json",
    "evolve-proposals-<date>.json",
]

_PR_E_CLASSIFICATION = {
    "evolve-state.json": "workflow_state",
    "remediation-outcomes.jsonl": "raw_event",
    "fleet-config.json": "workflow_state",
    "agent-brushup-state.json": "derived_cache",
    "skill-evolve-denylist.json": "workflow_state",
    "pj_slug_cache.json": "derived_cache",
    "skill-evolve-cache.json": "derived_cache",
    "evolve-queue.json": "derived_cache",
    "icebox-status.json": "derived_cache",
    "icebox-verdicts.json": "derived_cache",
    "evolve-proposals-<date>.json": "derived_cache",
}

# .json（単一オブジェクト・非 jsonl）basename の宣言は kind="json" が必須（#399 round1 Should 1）。
_PR_E_JSON_KIND_STORES = [
    name for name in _PR_E_STORES if name.endswith(".json")
]


def test_pr_e_stores_declared() -> None:
    """#379 Step 4 PR E: 未登録だった live store を宣言バックフィルする（issue #379 本文）。"""
    declared_names = {d.name for d in store_registry.declarations()}
    for name in _PR_E_STORES:
        assert name in declared_names, f"{name} が未宣言"


def test_pr_e_stores_are_active_with_expected_classification() -> None:
    """PR E 追加分は status=active（既定）・classification が実態に整合する。"""
    by_name = {d.name: d for d in store_registry.declarations()}
    for name, expected_classification in _PR_E_CLASSIFICATION.items():
        decl = by_name[name]
        assert decl.status == "active", name
        assert decl.classification == expected_classification, name
        assert decl.writer, f"{name}: writer 未記述"
        assert decl.reader, f"{name}: reader 未記述"


def test_pr_e_json_named_stores_have_kind_json() -> None:
    """PR E 追加分の `.json` basename は全て kind="json"（#399 round1 Should 1）。"""
    by_name = {d.name: d for d in store_registry.declarations()}
    for name in _PR_E_JSON_KIND_STORES:
        assert by_name[name].kind == "json", name


def test_pr_e_stores_pass_validate_declarations() -> None:
    """新規宣言（7+4件）を含めて宣言 SoT 自身の整合性が壊れていない。"""
    problems = store_registry.validate_declarations()
    assert problems == [], problems


def test_pr_e_stores_included_in_active_store_names() -> None:
    active = set(store_registry.active_store_names())
    for name in _PR_E_STORES:
        assert name in active, name


def test_active_store_names_unchanged_by_121() -> None:
    """#121: legacy/dead 追加で active 集合（write-path-set snapshot）は不変。"""
    active = set(store_registry.active_store_names())
    assert active.isdisjoint(set(_LEGACY_STORES_121) | set(_DEAD_STORES_121))


def test_legacy_dead_stores_not_flagged_as_stale_121() -> None:
    """#121: legacy/dead 宣言は contract-drift の stale に誤検知されない（#55 status の意図）。

    stale = 宣言ありだが実 hook writer 不在。legacy/dead は writer が batch/直接 or
    退役済み（dead）なので hook writer 突合に出ないのは当然であり drift ではない。
    stale_exempt を status-aware にして誤検知を防ぐ。
    """
    drift = orphan_store.detect_store_contract_drift()
    for name in _LEGACY_STORES_121 + _DEAD_STORES_121:
        assert name not in drift.stale, name
    # real tree の stale は空を維持（全 legacy/dead は exempt/batch/db）。
    assert drift.stale == []


def test_stale_exempt_includes_non_active_121() -> None:
    """#121: 非 active（legacy/dead）ストアは stale_exempt に含まれる。"""
    exempt = set(store_registry.stale_exempt_names())
    assert set(_LEGACY_STORES_121) <= exempt
    assert set(_DEAD_STORES_121) <= exempt


def test_episodic_and_sessions_and_token_declared_as_db_121() -> None:
    """#121: 3 つの .db legacy ストアは kind='db' で宣言（hook-writer 突合の母集団外）。"""
    for name in ("episodic.db", "sessions.db", "token_usage.db"):
        decl = store_registry.declaration_for(name)
        assert decl is not None, name
        assert decl.kind == "db", name
        assert decl.status == "legacy", name


def test_status_can_be_legacy_or_dead() -> None:
    """status は legacy / dead を取れる（migration で read-only 化・削除予定を表す）。"""
    legacy = StoreDeclaration(
        name="x.jsonl",
        writer="w",
        reader="r",
        retention="permanent",
        classification="workflow_state",
        status="legacy",
    )
    dead = StoreDeclaration(
        name="y.jsonl",
        writer="w",
        reader="r",
        retention="permanent",
        classification="dead",
        status="dead",
    )
    assert legacy.status == "legacy"
    assert dead.status == "dead"


def test_active_store_names_returns_only_active_sorted() -> None:
    """active_store_names() は status=active のストア名のみソートして返す。"""
    decls = [
        StoreDeclaration(
            name="a.jsonl", writer="w", reader="r", retention="permanent", classification="raw_event"
        ),
        StoreDeclaration(
            name="b.jsonl",
            writer="w",
            reader="r",
            retention="permanent",
            classification="workflow_state",
            status="legacy",
        ),
        StoreDeclaration(
            name="c.jsonl",
            writer="w",
            reader="r",
            retention="permanent",
            classification="dead",
            status="dead",
        ),
    ]
    assert store_registry.active_store_names(decls) == ["a.jsonl"]


def test_is_active_store() -> None:
    """is_active_store は active 宣言のみ True（未登録 / 非 active は False）。"""
    assert store_registry.is_active_store("corrections.jsonl") is True
    assert store_registry.is_active_store("no_such_store.jsonl") is False


def test_is_dead_store(monkeypatch) -> None:
    """is_dead_store は status=dead 宣言のみ True（#379 Step 3 レビュー: writer ゲート）。

    未登録 / active / legacy は False。lookup 不能でも False（fail-open で書込継続）。

    #379 Step 4: growth-journal.jsonl（実 registry で唯一の dead ストアだった）を宣言ごと
    削除したため、is_dead_store 自体の判定ロジックは合成の dead 宣言で検証する
    （declaration_for が参照する _DECLARATIONS を差し替える・real registry 非依存）。
    """
    synthetic = [
        StoreDeclaration(
            name="synthetic-dead.jsonl",
            writer="test",
            reader="test",
            retention="permanent",
            classification="dead",
            status="dead",
        ),
    ]
    monkeypatch.setattr(store_registry, "_DECLARATIONS", synthetic)
    assert store_registry.is_dead_store("synthetic-dead.jsonl") is True
    assert store_registry.is_dead_store("corrections.jsonl") is False
    assert store_registry.is_dead_store("no_such_store.jsonl") is False


def test_validate_declarations_accepts_status() -> None:
    """status 付き宣言も validate_declarations を通過する（既存ルール非破壊）。"""
    decls = [
        StoreDeclaration(
            name="a.jsonl",
            writer="w",
            reader="r",
            retention="permanent",
            classification="workflow_state",
            status="legacy",
        ),
    ]
    assert store_registry.validate_declarations(decls) == []
