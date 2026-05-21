# System Architecture

> このファイルは SPEC.md から分離された詳細仕様です。
> 概要は [SPEC.md](../SPEC.md) を参照してください。

Last updated: 2026-05-19 (Phase 8-13 パッケージ化完了 + hooks 更新)

## コンポーネント構成

```
hooks/                  ← Observe 層（15個 + helpers、LLMコストゼロ）[ADR-002]
  common.py             ← scripts/lib/rl_common の re-exporter（後方互換）[ADR-019]
  observe.py            ← usage/errors/corrections 記録
  correction_detect.py  ← corrections 自動検出
  subagent_observe.py   ← subagents.jsonl 記録
  instructions_loaded.py← sessions テーブル [ADR-015] + Growth greeting（LLMコストゼロ）
  stop_failure.py       ← API エラー記録
  permission_denied.py  ← PermissionDenied hook（CC v2.1.89）errors.jsonl に記録
  save_state.py         ← Compaction 前の作業コンテキスト保存 [ADR-013]
  post_compact.py       ← Compaction 後の作業コンテキスト復元（systemMessage 注入）
  restore_state.py      ← セッション開始時の状態復元
  session_summary.py    ← セッションサマリー記録 + auto_trigger ゲート
  workflow_context.py   ← ワークフローコンテキスト記録
  detect-deferred-task.py ← Stop hook: AI の先送り提案を検出し subagent 即時委譲を促す（CLAUDE_PLUGIN_DATA env var 対応、v1.43.0 で repo 取り込み）
  file_changed.py       ← FileChanged hook（CC v2.1.83）CLAUDE.md/SKILL.md/rules 変更検知
  skill_triage_runner.py← Stop hook で skill-triage を非同期実行（Popen）
  tool_duration.py      ← Bash 実行時間を tool_durations.jsonl に記録（CC v2.1.119+）
  skill_activation_log.py← Skill PostToolUse — invocation_trigger（nested-skill/top-level）を skill_activations.jsonl に記録（CC v2.1.121+）
  post_tool_use_memory.py← Write/Edit 後に memory update_count を自動インクリメント（closes #151）

bin/                    ← bareコマンド CLI（19個）[ADR-019]
  rl-evolve, rl-audit, rl-discover, rl-prune, rl-reorganize
  rl-reflect, rl-handover, rl-optimize, rl-loop
  rl-backfill, rl-backfill-analyze, rl-backfill-reclassify, rl-audit-aggregate
  rl-fleet, rl-usage-log
  rl-score-noise        ← 採点ノイズ計測（軸別σ + epsilon 推奨値出力）
  rl-prompt-compare     ← Evaluator プロンプト A/B 比較
  rl-gain               ← ROI 可視化（推定節約時間・Growth Level・Efficiency meter）

skills/                 ← スキル定義（21個）
  evolve/               ← 3ステージ自律進化パイプライン
  discover/             ← パターン検出 + スキル候補生成
  reflect/              ← 修正フィードバック反映
  audit/                ← 環境健康診断
  optimize/             ← 直接パッチ最適化
  agent-brushup/        ← エージェント品質診断
  second-opinion/       ← Claude Agent セカンドオピニオン（codex 代替）
  handover/             ← セッション引き継ぎ + Deploy State 構造化 + SPEC.md 同期 + PreCompact 自動提案
  implement/            ← 構造化実装スキル（plan → 実装 → 計画準拠チェック → テレメトリ）。Standard モードはタスク境界で認知分離（context: fresh 相当）を宣言し、前タスクの実装詳細はメモリ参照でなく Read で確認する
  cleanup/              ← PR マージ・デプロイ後の後片付け（branches/worktrees/tmp dirs/Issues/Test plan）を個別承認→実行 [ADR-021]

scripts/lib/            ← 共通ロジック（14 パッケージ・116 モジュール）[ADR-019]
  audit/                ← 環境健康診断（11 サブモジュール: memory/gstack/quality/issues/classification/artifacts/usage/scope/sections/report/orchestrator）。`usage.py` に `aggregate_contribution_scores`（スキル別貢献スコア集計）を追加（v1.59.0）
  discover/             ← パターン検出 + スキル/ルール候補生成
  fleet/                ← 全 PJ 横断観測
  pipeline_reflector/   ← Self-Evolution コアモジュール（outcomes/calibration/proposals）
  pitfall_manager/      ← pitfall 品質ゲート + ライフサイクル
  prune/                ← スキル/ルール統廃合候補抽出 + import 依存検査（#25）。`detect_retirement_candidates` で貢献スコア閾値以下のスキルをアーカイブ候補として検出（v1.59.0）
  remediation/          ← confidence-based 問題分類 + 修正 + FP排除
  rl_common/            ← hooks 共通ユーティリティ（persistence.py / config.py / detection.py / false_positive.py 等）
  skill_evolve/         ← 自己進化パターン組み込み（llm_scoring / telemetry_scoring / classification / assessment / proposal）
  telemetry_query/      ← DuckDB 共通クエリ層（helpers / usage_errors / sessions / corrections / workflows）
  trigger_engine/       ← Auto-evolve trigger engine（state / session_corrections / file_change / bloat / self_evolution）
  verification_catalog/ ← 検証知見カタログ
  coherence/            ← 構造的整合性評価（scoring_basic / scoring_advanced / aggregation / artifacts）
  tool_usage_analyzer/  ← ツール使用状況分析
  （フラット単体モジュール: agent_quality, growth_engine, session_store, score_noise 等）

scripts/bench/          ← TBench2-rl Harness Quality Benchmark（Week 1-3 実装済み）
  golden_extractor.py   ← GoldenCase（正例/負例ペア）抽出 — usage.jsonl + corrections.jsonl
  output_evaluator.py   ← AxisScores + OutputEvaluator — 3軸採点（技術/ドメイン/構造）
  run_benchmark.py      ← BenchmarkRunner — 出力生成 → 採点 → benchmark_results.jsonl
  mutation_injector.py  ← MutationInjector（rule_delete/trigger_invert/prompt_truncate）+ SentinelRunner
  spike_*.py/json/md    ← rl-scorer 転用可否スパイク（Week 1 末検証）

scripts/rl/fitness/     ← 適応度関数（8個組み込み: default + 7 .py ファイル、config.py / principles.py は supporting）
  config.py             ← 全モジュール共有閾値 + BASE_WEIGHTS (supporting)
  principles.py         ← PJ固有原則抽出 + キャッシュ (supporting、constitutional.py から呼び出し)
  coherence.py          ← 環境 Coherence Score（4軸）
  telemetry.py          ← テレメトリ駆動 Score（3軸）
  constitutional.py     ← 原則ベース LLM Judge + /cso security 軸
  chaos.py              ← 仮想除去ロバストネス
  environment.py        ← 動的重み統合（_normalize_weights + skill_quality 4軸目）
  skill_quality.py      ← ルールベース構造品質
  plugin.py             ← rl-anything プラグイン統合 fitness
  （default は LLM 汎用評価で専用ファイルなし）
```

## データフロー

```
ユーザー操作
  → Observe hooks (自動記録、LLMコストゼロ)
    → usage.jsonl / errors.jsonl / corrections.jsonl / sessions.jsonl
      → discover (パターン検出)
      → evolve (Diagnose → Compile → Housekeeping)
        → remediation (問題分類 → 修正 → 検証)
      → reflect (corrections → rules/CLAUDE.md 反映)
      → audit (環境健康診断)
      → optimize (直接パッチ → regression gate)
      → instruction compliance (corrections × critical指示 → 違反検出 → pitfall学習)
      → growth_engine (Phase判定 → growth-state.json キャッシュ)
        → growth_journal (結晶化イベント記録)
        → growth_narrative (環境プロファイル + 成長ストーリー)
  → InstructionsLoaded hook (growth-state.json → Growth greeting stdout)
```

## Key Design Decisions カテゴリ別サマリ

全 22 件の詳細は [docs/decisions/](../docs/decisions/) を参照。SPEC.md からの移動（2026-04-24）。

- **配布・観測**: Plugin 配布 ([001](../docs/decisions/001-plugin-distribution-model.md)), hooks+JSONL ([002](../docs/decisions/002-observe-hooks-jsonl-architecture.md)), hook enrichment ([015](../docs/decisions/015-hook-agent-enrichment.md)), Plugin bin/ 移行 ([019](../docs/decisions/019-plugin-bin-directory-migration.md)), philosophy seed 配布 ([020](../docs/decisions/020-philosophy-seed-distribution.md))
- **パイプライン**: GA廃止→直接パッチ ([003](../docs/decisions/003-direct-patch-over-genetic-algorithm.md)), 全レイヤー診断/Compile ([007](../docs/decisions/007-all-layer-diagnose-adapter-pattern.md), [008](../docs/decisions/008-all-layer-compile-dispatch-pattern.md)), 3ステージ簡素化 ([009](../docs/decisions/009-simplify-pipeline-3-stage.md)), スキル自己進化 ([016](../docs/decisions/016-skill-self-evolution-pattern.md), [017](../docs/decisions/017-evolve-skill-independent-command.md))
- **評価・スコアリング**: Coherence 4軸 ([004](../docs/decisions/004-coherence-score-4-axes.md)), Telemetry ([005](../docs/decisions/005-telemetry-score-architecture.md)), Constitutional Judge ([006](../docs/decisions/006-constitutional-eval-llm-judge.md)), CoT除去 ([018](../docs/decisions/018-evaluate-pipeline-cot-removal.md))
- **運用・自動化**: Auto trigger ([010](../docs/decisions/010-auto-evolve-trigger-engine.md), [011](../docs/decisions/011-auto-compression-trigger.md)), Self-Evolution EWA ([012](../docs/decisions/012-self-evolution-trajectory-ewa.md)), Compaction復元 ([013](../docs/decisions/013-compaction-state-recovery.md)), CC v2適用 ([014](../docs/decisions/014-adopt-claude-code-v2-features.md))
- **安全設計**: Cleanup tmp_dir prefix safety-first default ([021](../docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))
- **アーキテクチャ拡張**: fleet 観測＋介入を同一プラグインに統合（4 本目の柱）([022](../docs/decisions/022-fleet-observation-plus-intervention.md))
