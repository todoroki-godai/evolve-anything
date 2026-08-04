"""shrink_freeze.py — #379 Step 1「新設凍結」の単一ソース判定契約。

ミニマルコアへの縮小方針が確定した（#379）。縮小完了まで新 store / observability
section / advisory proposal adapter / weak_signal channel の追加を止める（削除は許容
する — 縮小方向は常に通す）。凍結なき縮小は「穴の空いたバケツ」になるため、削除
（Step 3/4）に先立ってまず追加を止めるのが Step 1 の狙い。

FROZEN_* は本モジュール実装時点（#379 Step 1 PR）の各レジストリの正規スナップショット。
実装後に増えたキー（＝新設）だけが `assert_no_new_keys` で reject される。削除方向
（live 側が FROZEN_* の部分集合になる方向）は常に許容する。

凍結解除手順: 縮小完了・ユーザー判断確定時に ``SHRINK_FREEZE_ACTIVE`` を ``False`` に
変更する（#379 参照）。解除後は本モジュールの契約テストが自動的に skip 相当（全 assert
が no-op）になる。FROZEN_* の凍結スナップショット自体は解除後も履歴として残してよい。

消費側への注意（module-level import copy pitfall）: ``SHRINK_FREEZE_ACTIVE`` を
``from shrink_freeze import SHRINK_FREEZE_ACTIVE`` で import 時にコピーすると
monkeypatch/テストの差し替えに追従しない（pitfall_module_level_datadir_import_copy と
同型）。消費側は必ず ``shrink_freeze.is_frozen()``（call-time 参照）を使うこと。
"""
from __future__ import annotations

from typing import FrozenSet, Iterable

# #379 Step 1 の凍結フラグ。縮小完了までは True のまま維持する。
SHRINK_FREEZE_ACTIVE: bool = True

# 実装時点（#379 Step 1）の store_registry 全宣言 name（active/legacy/dead 全部含む）。
FROZEN_STORES: FrozenSet[str] = frozenset(
    {
        "advisory_decisions.jsonl",
        "audit-history.jsonl",
        "belief_blocks.jsonl",
        "bootstrap_done-<slug>.marker",
        "correction_idioms.jsonl",
        "correction_judged.jsonl",
        "correction_review_seen.jsonl",
        "corrections.jsonl",
        "deferred_tasks.jsonl",
        "discover-suppression.jsonl",
        "episodic.db",
        "errors.jsonl",
        "evolution_memory.jsonl",
        "evolve-queue-state.jsonl",
        "false_positives.jsonl",
        "growth-journal.jsonl",
        "icebox_verdict_seen.jsonl",
        "judge_audit_verdicts.jsonl",
        "memory_transition_checks.jsonl",
        "quality-baselines.jsonl",
        "quality-scores.jsonl",
        "remediation_suppression/<slug>.jsonl",
        "remediation_surfaced/<slug>.json",
        "reward_ema.jsonl",
        "sessions.db",
        "sessions.jsonl",
        "skill_activations.jsonl",
        "subagent_traces.jsonl",
        "subagents.jsonl",
        "token_usage.db",
        "usage-registry.jsonl",
        "usage.jsonl",
        "utterances.db",
        "verbosity_candidates.jsonl",
        "verbosity_verdicts.jsonl",
        "weak_signals.jsonl",
        "workflows.jsonl",
    }
)

# 実装時点（#379 Step 1）の audit/observability.py _OBSERVABILITY_BUILDERS 全キー。
FROZEN_OBSERVABILITY_SECTIONS: FrozenSet[str] = frozenset(
    {
        "advisory_decisions",
        "agent_team",
        "agent_tier",
        "backup_files",
        "belief_blocks",
        "calibration_drift",
        "correction_capture",
        "doc_budget",
        "duplicate_skill_names",
        "eval_saturation",
        "fanout_cost",
        "global_claude_md",
        "global_hook_plugin_dup",
        "glossary_drift",
        "hook_drift",
        "icebox_reconcile",
        "invalid_frontmatter",
        "judge_audit",
        "measurement_bug",
        "memory_capability",
        "memory_contagion",
        "memory_contamination",
        "memory_dup_residue",
        "memory_index_orphan",
        "memory_schema",
        "missing_skill_md",
        "multiview_eval",
        "negative_transfer",
        "orphan_store",
        "outcome_metrics",
        "paired_trajectory",
        "promotion_readiness",
        "self_contamination",
        "skill_reachability",
        "skill_triage",
        "skill_vuln",
        "store_contract",
        "subagent_noise",
        "subagent_traces",
        "testpaths_coverage",
        "unmanaged_pitfalls",
        "verbosity",
        "weak_signals",
        "worker_takeoff",
    }
)

# 実装時点（#379 Step 1）の advisory_proposals.py ADVISORY_PROPOSAL_ADAPTERS 全キー。
FROZEN_ADVISORY_PROPOSAL_ADAPTERS: FrozenSet[str] = frozenset(
    {
        "invalid_frontmatter",
        "testpaths_coverage",
    }
)

# 実装時点（#379 Step 1）の weak_signal channel 全種別（correction_semantic/review_channels.py
# の REVIEW_CHANNELS | CONTENT_POOR_CHANNELS が正準の機械可読列挙）。
FROZEN_WEAK_SIGNAL_CHANNELS: FrozenSet[str] = frozenset(
    {
        "esc_interrupt",
        "llm_judge",
        "manual_edit_after_ai",
        "permission_deny",
        "rephrase",
        "verbosity",
    }
)


def is_frozen() -> bool:
    """凍結が有効かを call-time 判定で返す（module-level コピー禁止）。"""
    return SHRINK_FREEZE_ACTIVE


def assert_no_new_keys(current: Iterable[str], frozen: FrozenSet[str], kind: str) -> None:
    """current が frozen に無い新規キーを含んでいたら AssertionError（凍結時のみ）。

    削除方向（current が frozen の部分集合）は常に許容する。凍結解除中（is_frozen()
    が False）は何もチェックしない（縮小方針が変わったときの将来経路）。
    """
    if not is_frozen():
        return
    extra = set(current) - set(frozen)
    if extra:
        raise AssertionError(
            f"{kind}: 新規追加を検出しました {sorted(extra)}。"
            "#379 Step 1 新設凍結中。本当に必要なら SHRINK_FREEZE_ACTIVE の解除判断を"
            "ユーザーに仰ぐこと"
        )
