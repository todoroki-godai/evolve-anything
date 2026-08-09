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
（test_shrink_freeze.py はこの解除手順の実行自体を機構テストで検証しており、フラグを
False にしてもテスト自体は落ちない契約になっている。）

消費側への注意（module-level import copy pitfall）: ``SHRINK_FREEZE_ACTIVE`` を
``from shrink_freeze import SHRINK_FREEZE_ACTIVE`` で import 時にコピーすると
monkeypatch/テストの差し替えに追従しない（pitfall_module_level_datadir_import_copy と
同型）。消費側は必ず ``shrink_freeze.is_frozen()``（call-time 参照）を使うこと。

強制の実体（3層・各層の守備範囲は限定的）:
  1. **CI（blocking）** — ``scripts/lib/tests/test_shrink_freeze.py`` が
     ``.github/workflows/ci.yml`` の portable contract suite に配線済み。PR/push で
     ``store_registry`` / ``audit.observability._OBSERVABILITY_BUILDERS`` /
     ``advisory_proposals.ADVISORY_PROPOSAL_ADAPTERS`` /
     ``weak_signals.channels.WEAK_SIGNAL_CHANNELS`` の live 集合を検証する唯一の
     **実効ゲート**（赤で PR がブロックされる）。
  2. **pre-push light（非ブロッキング早期警告）** — ``dogfood.cli._run_shrink_freeze_advisory``
     が同じ4集合を push 前にローカルで先出しするが、exit code には一切影響しない
     （skill_reachability / doc_budget advisory と同型）。push 自体は止めない。
  3. **runtime ゲート（書込み境界）** — ``weak_signals.store.append_signals`` と
     ``rl_common.store_write.store_write_raw``（正準 DATA_DIR 配下に限る）が凍結中の
     未登録書込みを reject する。ただしこれは「weak_signal channel」と「store basename」
     の2軸のみをカバーし、observability section / advisory proposal adapter の新設は
     runtime では検出しない（コードレビューと CI 契約テストが頼り）。
"""
from __future__ import annotations

from typing import FrozenSet, Iterable

# #379 Step 1 の凍結フラグ。縮小完了までは True のまま維持する。
SHRINK_FREEZE_ACTIVE: bool = True

# 実装時点（#379 Step 1）の store_registry 全宣言 name（active/legacy/dead 全部含む）。
#
# #379 Step 4 PR E で7件追加（evolve-state.json / remediation-outcomes.jsonl /
# fleet-config.json / agent-brushup-state.json / skill-evolve-denylist.json /
# pj_slug_cache.json / skill-evolve-cache.json）。これらは Step 1 凍結より前から
# 実際に書き込まれ続けていた live store で、store_registry への宣言が単に漏れて
# いただけ（#121 の legacy backfill と同型）。凍結の趣旨は「新しい書込経路・新しい
# 機能を作らせない」ことであり、既存の実ファイル・既存の writer/reader コードを
# registry へ追認する本件は「新設」ではない（詳細は store_registry.py 側の
# StoreDeclaration note 参照）。
FROZEN_STORES: FrozenSet[str] = frozenset(
    {
        "advisory_decisions.jsonl",
        "agent-brushup-state.json",
        "audit-history.jsonl",
        "belief_blocks.jsonl",
        "bootstrap_done-<slug>.marker",
        "correction_idioms.jsonl",
        "correction_judged.jsonl",
        "correction_review_seen.jsonl",
        "corrections.jsonl",
        "discover-suppression.jsonl",
        "episodic.db",
        "errors.jsonl",
        "evolution_memory.jsonl",
        "evolve-queue-state.jsonl",
        "evolve-state.json",
        "false_positives.jsonl",
        "fleet-config.json",
        "icebox_verdict_seen.jsonl",
        "memory_transition_checks.jsonl",
        "pj_slug_cache.json",
        "quality-baselines.jsonl",
        "remediation-outcomes.jsonl",
        "remediation_suppression/<slug>.jsonl",
        "remediation_surfaced/<slug>.json",
        "reward_ema.jsonl",
        "sessions.db",
        "sessions.jsonl",
        "skill-evolve-cache.json",
        "skill-evolve-denylist.json",
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

# 実装時点（#379 Step 1）の weak_signal channel 全種別（weak_signals.channels.WEAK_SIGNAL_CHANNELS
# が producer 側正準の機械可読列挙。correction_semantic/review_channels.py の
# REVIEW_CHANNELS | CONTENT_POOR_CHANNELS はその部分集合＝消費側の分類にすぎない・修正3）。
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


# #379 Step 2「表示淘汰」の単一ソース。人間の行動（accept/reject 等）に繋がった実証の
# ない observability section を audit の**表示からのみ**外す（コードは削除しない・builder も
# _OBSERVABILITY_BUILDERS に登録されたまま）。除外は audit/observability.py の
# collect_observability() が本集合を参照してループ内 skip するだけで、ここに列挙した
# key は他のどこからも import 削除されない。解除は本集合から key を除くだけでよい
# （skill 自体の削除・非活性化ではない）。
#
# 対象は FROZEN_OBSERVABILITY_SECTIONS（44 キー）から下記 KEEP 11 件を除いた 33 件のうち、
# judge_audit を #379 Step 4 で harness ごと宣言削除したため現在は 32 件
# （2026-08-04 ユーザー合意 #379、2026-08-05 レビュー指摘で再分類）。KEEP 11 の内訳:
#   ①行動配線の実証あり: correction_capture / skill_triage / skill_reachability /
#     doc_budget / icebox_reconcile / measurement_bug（evolve_introspect の起票レーン
#     #324 が構造化出力から読む）/ glossary_drift（evolve SKILL.md Step 7.7 の seed
#     提案フローが構造化出力から読む）
#   ②ミニマルコア表示面: weak_signals
#   ③例外（①②と同列でない）: advisory_decisions（accept 記録 0 件だが淘汰基準の台帳
#     自身＝基準観測の基盤として KEEP）
#   ④例外（①②と同列でない）: rare-event 安全系は accept-0 淘汰の適用外
#     （skill_vuln / invalid_frontmatter）
#
# エスケープハッチ: 環境変数 ``EVOLVE_SHOW_CULLED=1`` で一時的に全表示へ戻せる
# （collect_observability が call-time に os.environ を読む・Step 3 の棚卸しや
# デバッグ用）。
CULLED_OBSERVABILITY_SECTIONS: FrozenSet[str] = frozenset(
    {
        "agent_team",
        "agent_tier",
        "backup_files",
        "belief_blocks",
        "calibration_drift",
        "duplicate_skill_names",
        "eval_saturation",
        "fanout_cost",
        "global_claude_md",
        "global_hook_plugin_dup",
        "hook_drift",
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
        "store_contract",
        "subagent_noise",
        "subagent_traces",
        "testpaths_coverage",
        "unmanaged_pitfalls",
        "verbosity",
        "worker_takeoff",
    }
)


class FreezeViolationError(AssertionError):
    """凍結中に新設を検出したときの例外。

    ``AssertionError`` のサブクラスなので既存の ``pytest.raises(AssertionError, ...)`` は
    そのまま通る。``assert_no_new_keys``（テスト時契約）と runtime ゲート
    （``weak_signals.store.append_signals`` / ``rl_common.store_write.store_write_raw``）が
    同じ例外型を共有し、「凍結違反」を検出箇所によらず一貫した型で扱えるようにする。
    """


def is_frozen() -> bool:
    """凍結が有効かを call-time 判定で返す（module-level コピー禁止）。"""
    return SHRINK_FREEZE_ACTIVE


def assert_no_new_keys(current: Iterable[str], frozen: FrozenSet[str], kind: str) -> None:
    """current が frozen に無い新規キーを含んでいたら FreezeViolationError（凍結時のみ）。

    削除方向（current が frozen の部分集合）は常に許容する。凍結解除中（is_frozen()
    が False）は何もチェックしない（縮小方針が変わったときの将来経路）。
    """
    if not is_frozen():
        return
    extra = set(current) - set(frozen)
    if extra:
        raise FreezeViolationError(
            f"{kind}: 新規追加を検出しました {sorted(extra)}。"
            "#379 Step 1 新設凍結中。本当に必要なら SHRINK_FREEZE_ACTIVE の解除判断を"
            "ユーザーに仰ぐこと"
        )
