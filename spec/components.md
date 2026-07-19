# コンポーネント詳細（索引）

CLAUDE.md のコンポーネント表の詳細版（SoT）。分量が大きいため4ファイルに分割済み。
コンポーネント名で本ファイルを Grep し、該当ドメインファイルを部分 Read すること。

**運用ルール**: 新コンポーネント追加・既存変更時は、該当ドメインのファイルに詳細を書き、
CLAUDE.md のサマリ表には 1 行（名前 + 一言 + 参照）だけ追記する。新ドメインに該当しない
場合は最も近いテーマのファイルに追記するか、ユーザーに新ファイル追加の要否を確認する。

## コア進化エンジン・ストア基盤 → [components-core.md](components-core.md)

Observe hooks, Auto Trigger, userConfig, genetic-prompt-optimizer, evolve-loop-orchestrator,
variant_generation, selection_reeval, loop_ablation, evolve-scorer, skill-triage,
tool_usage_analyzer, trigger-eval-generator, evolve-skill, agent-brushup,
critical-instruction-compliance, second-opinion, growth-level, optimize_history_store,
evolve_decisions, evolve_reconcile, token_usage_store, token_usage_ingest, token_usage_query,
auto_memory_runner, auto_memory_broker, meta_quality, triage_ledger, constraint_decay,
negative_transfer, eval_saturation, subgoal_scorer, evolution_operators, memory_trace,
slop_detector, skill_extractor, skill_rm

## observability・検出器・ストア契約 → [components-observability.md](components-observability.md)

pitfall自動強制, agent_team, observability contract, scaffold_advisory, evolve_introspect,
evolve_result_schema, evolve_consistency, hook_drift, data_dir_migration, spec_trigger,
outcome_metrics, outcome_attribution, reward_ema, subagent_traces, subagent_noise, verbosity,
capture_rate, orphan_store, utterance_archive, SessionStore, reader union網羅監査,
store_registry, store_write

## 修正フィードバック・weak_signals系 → [components-feedback.md](components-feedback.md)

weak_signals, correction_semantic, bootstrap_backlog, daily_review, review_channels,
idiom_autopromote, measurement_bug, growth_report, outcome_promotion_readiness,
cross_pj_priority, plugin_self, testpaths_coverage, dogfood gate, evolve-release-sync,
pj_slug, weak_signals drain永続化, idiom_filter, representative, multiview_eval,
relevance_gate, remediation参照リンク相対化, report-feedback, paired_trajectory

## fleet運用・記憶温度・環境衛生検出器 → [components-fleet.md](components-fleet.md)

recall link 1-hop, recall validity-aware ranking, reinforce_memory配線, memory_capability,
skill_vuln_scan, fanout_cost, memory_contagion, memory_guard, fleet_queue, daily,
icebox_notice, artifacts_hygiene, memory_hygiene, invalid_frontmatter, self_contamination,
evolve_tier, tier_skill, judge_audit, worker_takeoff, skill_reachability, fleet_propose,
fleet_pr
