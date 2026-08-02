# System Architecture

> このファイルは SPEC.md から分離された詳細仕様です。
> 概要は [SPEC.md](../SPEC.md) を参照してください。

Last updated: 2026-06-05 (ADR-038: subagent_observe additionalContext)

## コンポーネント構成

```
hooks/                  ← Observe 層（16個 + helpers、LLMコストゼロ）+ pitfall 自動強制（pitfall_lint / pitfall_commit_gate、警告 or block）[ADR-002, ADR-027]
  common.py             ← scripts/lib/rl_common の re-exporter（後方互換）[ADR-019]
  observe.py            ← usage/errors/corrections 記録
  correction_detect.py  ← corrections 自動検出
  subagent_observe.py   ← subagents.jsonl 記録 + 閾値超過警告（systemMessage + additionalContext、ADR-038）
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
  skill_activation_log.py← Skill PostToolUse — invocation_trigger（nested-skill/top-level）を skill_activations.jsonl に記録（CC v2.1.121+）
  post_tool_use_memory.py← Write/Edit 後に memory update_count を自動インクリメント（closes #151）
  auto_memory_runner.py ← Stop hook（ゼロ LLM, ADR-037 Phase 2）: corrections 直近5件 → memory-gating（生成前ゲート、coherence 40% + novelty 40% + recency 20% で composite < 0.5 をスキップ、`RL_GATING_DISABLED=1` で bypass）→ 生き残りを内容ハッシュ dedup で PJ スコープキュー `DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue するのみ。LLM 生成・生成後ゲート（belief_entropy）・memory 書込（new-file-per-entry）は auto_memory_broker.py の2相（emit→assistant インライン→ingest）が evolve drain で担う
  auto_memory_broker.py ← auto-memory の2相ブローカ（ADR-037 Phase 2）: emit_memory_requests（Phase A）/ ingest_memory_results（Phase C: belief ゲート + 全ファイル書込 + キュー消化）+ enqueue/read_queue/clear_queue_entries（PJ スコープ jsonl ストア、内容ハッシュ dedup）。claude subprocess ゼロ
  pitfall_lint.py       ← PostToolUse(Edit/Write/MultiEdit): enable 登録済み pitfalls.md の編集時に正準フォーマットを lint。警告のみ・ブロックしない（編集途中の中間状態を踏むため）[ADR-027]
  pitfall_commit_gate.py← PreToolUse(Bash): `git commit` 検知時に staged な管理対象 pitfalls.md を lint。danger（index/TOC wipe 危険）は exit 2 でブロック、drift は警告のみ。run_git 注入可（テストは subprocess 不要）[ADR-027]

bin/                    ← bareコマンド CLI（18個）[ADR-019]
  evolve, evolve-audit, evolve-discover, evolve-prune, evolve-reorganize
  evolve-reflect, evolve-optimize, evolve-loop
  rl-backfill, rl-backfill-analyze, rl-backfill-reclassify, evolve-audit-aggregate
  evolve-fleet, evolve-usage-log
  evolve-score-noise        ← 採点ノイズ計測（軸別σ + epsilon 推奨値出力）
  evolve-prompt-compare     ← Evaluator プロンプト A/B 比較
  evolve-gain               ← ROI 可視化（推定節約時間・Growth Level・Efficiency meter）

skills/                 ← スキル定義（20個）
  evolve/               ← 3ステージ自律進化パイプライン
  discover/             ← パターン検出 + スキル候補生成
  reflect/              ← 修正フィードバック反映
  audit/                ← 環境健康診断
  optimize/             ← 直接パッチ最適化
  agent-brushup/        ← エージェント品質診断
  second-opinion/       ← セカンドオピニオン（既定=Claude Agentルート、codex検出時は外部ルートも選択可）  implement/            ← 構造化実装スキル（plan → 実装 → 計画準拠チェック → テレメトリ）。Standard モードはタスク境界で認知分離（context: fresh 相当）を宣言し、前タスクの実装詳細はメモリ参照でなく Read で確認する
  cleanup/              ← PR マージ・デプロイ後の後片付け（branches/worktrees/tmp dirs/Issues/Test plan）を個別承認→実行 [ADR-021]

scripts/lib/            ← 共通ロジック（14 パッケージ・122 モジュール）[ADR-019]
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
  （フラット単体モジュール: agent_quality, growth_engine, session_store, score_noise, corrections_insights 等）
    corrections_insights.py ← コーパスレベル診断。corrections.jsonl の繰り返し失敗パターン TOP-N 集計。audit セクションに自動表示（件数閾値 MIN_DISPLAY_RECORDS=10、_POSITIVE_TYPES を CORRECTION_PATTERNS から動的導出）
    meta_quality.py         ← skill_triage CREATE 判定パスの品質フィルタ。再利用頻度（reuse_rate）と Jaccard 類似度で CREATE/REVIEW/SKIP を判定しスキルバブル防止
    similarity.py           ← Jaccard 係数計算共通ユーティリティ（tokenize + jaccard_coefficient。skill_triage / meta_quality から使用）
    trigger_eval_generator.py ← sessions.jsonl + usage.jsonl → skill-creator 互換 evals.json 自動生成（EVAL_SETS_DIR、MIN_EVAL_QUERIES）
    skill_triage.py         ← テレメトリ + trigger eval で CREATE/UPDATE/SPLIT/MERGE/OK の5択ライフサイクル判定
    fitness_history_store.py← DuckDB 冪等 ingest（ON CONFLICT DO NOTHING）fitness スコア履歴 SoR。`record_fitness_run(run_id, axis_scores, weights)` が NaN ガード付きで各軸を記録し `environment.py` が `record=True` 時に呼び出す（v1.71.x）
    hypothesis_tracker.py   ← VeriTrace Phase 1。仮説ツリーを JSONL で永続化（save/load/update_confidence/detect_contradiction）。write-then-rename アトミックパターン（v1.71.x）
    skill_extractor/        ← SIRI ① 成功軌跡採掘（#238 Phase 1 実装 → #291 配線）。trajectory_sampler.py: raw セッション JSONL から TrajectoryRecord 抽出（<command-name> タグ検出・ストリーミング読み込み・outcome 判定 success/unknown）。skill_extractor.py: skill 別グループ化 + generalizability_score 算出で missed_skills 互換候補を生成。`run_discover` が project スコープ（`_project_transcript_dir` で CC エンコード変換）で発火し、`TRAJECTORY_SKILL_SCORE_THRESHOLD` でフィルタして `trajectory_skill_candidates` を surface しつつ triage の missed_skill_opportunities へ合流。LLM 非依存。discover=evolve recurring ループに配線済（#291、[ADR-030](../docs/decisions/030-skill-extractor-discover-wiring-project-scoped.md)）
    subgoal_scorer.py       ← BES 後ろ向き分解（#253）。候補テキストを 5 サブゴール（frontmatter_preserved / trigger_coverage / correction_addressed / line_budget / slop_free）に分解して密な中間フィードバックを返す。`optimize_core.run_subgoal_scoring` がラップ。LLM 非依存・決定論。slop_free は slop_detector に接続（#255）
    evolution_operators.py  ← BES 前向き進化探索（#256）。crossover（## セクション単位結合・frontmatter は parent_a 保持）/ mutate（安定ソート + 連続重複行除去 + corrections 強調）/ select_parents（fitness-proportional ルーレット・全 0/負で一様 fallback・rng 注入で再現可能）/ evolve_generation。evolve-loop の `--evolve-search` が consume。LLM 非依存・決定論
    memory_trace.py         ← MemTrace 帰属診断（#254）。episodic memory 検索エラーを misretrieval（低スコア）/ context_drift（temporal staleness）/ corruption（検索直後 correction）の3類型に分類し発生源 event_id に帰属。LLM・外部 oracle 不使用、DuckDB 未インストール時は空返し。`audit/memory.py` が利用
    slop_detector.py        ← AI slop 辞書検出（#255）。決定論 regex/ヒューリスティックで日英 10 パターン（過度な肯定・不要な謝罪・無意味な要約見出し・過剰な免責・空虚な接続句）を検出。`detect_slop(text) -> SlopResult(slop_score, hits)`（1.0=良 / 0.0=悪）。constitutional.py が 10% 加重ブレンド、subgoal_scorer が slop_free 判定に使用
    pitfall_registry.py     ← pitfall-curate 自動強制のオプトイン台帳（#265）。`enable`/`disable` で `.claude/evolve-anything/pitfall-managed.json` を読み書き（add/remove/is_managed/load）。キーは project 相対、外部は絶対パス。決定論・LLM 非依存。pitfall_lint / pitfall_commit_gate hook が参照し、登録済みファイルにのみ反応する

scripts/bench/          ← TBench2-rl Harness Quality Benchmark（Week 1-3 実装済み）
  golden_extractor.py   ← GoldenCase（正例/負例ペア）抽出 — usage.jsonl + corrections.jsonl
  output_evaluator.py   ← AxisScores + OutputEvaluator — 3軸採点（技術/ドメイン/構造）
  run_benchmark.py      ← BenchmarkRunner — 出力生成 → 採点 → benchmark_results.jsonl
  mutation_injector.py  ← MutationInjector（rule_delete/trigger_invert/prompt_truncate）+ SentinelRunner
  spike_*.py/json/md    ← evolve-scorer 転用可否スパイク（Week 1 末検証）

scripts/rl/fitness/     ← 適応度関数（8個組み込み: default + 7 .py ファイル、config.py / principles.py は supporting）
  config.py             ← 全モジュール共有閾値 + BASE_WEIGHTS (supporting)
  principles.py         ← PJ固有原則抽出 + キャッシュ (supporting、constitutional.py から呼び出し)
  coherence.py          ← 環境 Coherence Score（4軸）
  telemetry.py          ← テレメトリ駆動 Score（3軸）
  constitutional.py     ← 原則ベース LLM Judge + /cso security 軸 + slop_detector 10% 加重ブレンド（#255、import 失敗時は overall 素通し）
  chaos.py              ← 仮想除去ロバストネス
  environment.py        ← 動的重み統合（_normalize_weights + skill_quality 4軸目）
  skill_quality.py      ← ルールベース構造品質
  plugin.py             ← evolve-anything プラグイン統合 fitness
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
      → reflect (corrections → rules/CLAUDE.md 反映。importance_score で低重要度をフィルタ [Mem-π])
      → corrections_insights (繰り返し失敗パターン TOP-N 集計 → audit セクションに自動表示)
      → constraint_decay (セッション後半 30% ターン集中 correction を検出 → decay_rate 算出)
      → negative_transfer (スキル追加前後の success rate delta 計測 → delta < -0.05 でフラグ。compute_component_transfer は更新コンポーネント別に isolation window で分離帰属し observability contract で surface、#288)
      → audit (環境健康診断)
      → self_analysis (evolve_introspect: run_evolve 末尾で result 全体を読み 3 カテゴリ
          [提案矛盾 / phase 例外 / 系統的却下] の issue 候補を生成 → SKILL Step 11 が人間承認後
          todoroki-godai/evolve-anything へ半自動起票。root cause 単位の body マーカーで重複起票防止、#299 [ADR-033])
      → optimize (直接パッチ → regression gate)
      → instruction compliance (corrections × critical指示 → 違反検出 → pitfall学習)
      → growth_engine (Phase判定 → growth-state.json キャッシュ)
        → growth_journal (結晶化イベント記録)
        → growth_narrative (環境プロファイル + 成長ストーリー)
  → InstructionsLoaded hook (growth-state.json → Growth greeting stdout)
```

## Observe / 報酬信号レイヤー詳細

> SPEC.md から移動（2026-08-02、cold 移動・35KB 閾値超過対応）。

Observe hooks (24個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。**報酬信号レイヤー（#430-#432, #415）**: hooks は jsonl 追記のみで DuckDB 書き込みは batch ingest に限定（jsonl-first、sessions.db 680倍 bloat の根治）。`utterance_archive` が全PJ human 発話を utterances.db に恒久化（transcript の cleanupPeriodDays 消失対策）、`weak_signals` が暗黙修正4チャネル（直後手編集/permission deny/言い直し/Esc中断）を決定論検出、`correction_semantic` が Haiku バッチ意味判定で文中・後置・観察型の修正を発掘し weak_signals(channel=llm_judge) に隔離。corrections への昇格は reflect の人間確認後のみ、フェーズ昇格カウントは human-source 限定。ストア新設は `store_registry` の writer/reader/retention 宣言が必須（#434 事前契約ゲート）。詳細は [spec/components.md](components.md)。UserPromptSubmit に HASP-style pitfall_injector 追加（エラー閾値検知で pitfalls.md を自動 inject）。Stop hook の `auto_memory_runner` は corrections を生成前ゲート（memory_gating）して PJ スコープキュー `DATA_DIR/auto_memory_queue/<slug>.jsonl` に内容ハッシュ dedup で enqueue するだけのゼロ LLM 化済み（[ADR-037](../docs/decisions/037-eliminate-claude-p-consolidate-llm-into-interactive-evolve.md) Phase 2 #327）。LLM 生成・生成後ゲート（belief_entropy）・memory 書込は `auto_memory_broker` の2相（emit→assistant インライン→ingest）が evolve drain（SKILL Step 6.5）で担い、Stop hook から `claude -p` を全廃。生成後は `belief_entropy` 決定論ゲート（生成要約の retention/drift を similarity 集合演算で近似採点、LLM ゼロ）が低信頼要約を書込前に破棄し `belief_blocks.jsonl` へ記録（#285）。SubagentStop hook（`subagent_observe`）は **直近 `subagent_window_minutes`（既定5分）以内の同一セッション subagent 生成数**が閾値（`subagent_warning_threshold`=5）に達した警告を `systemMessage`〔user 向け〕に加え `hookSpecificOutput.additionalContext`〔Claude 向け、CC v2.1.163〕でも出し、subagent-guard.md の「閾値超過で作業を一時停止しユーザー説明」を実エンフォース。累積でなく時間窓で測ることで長時間セッションの正常使用を誤検知せず短時間バーストの暴走ループ/カスケードだけを捕捉する（Stop hook の additionalContext は非介入方針と衝突するため HOLD、[ADR-038](../docs/decisions/038-stop-hook-additional-context-subagentstop-only.md)）。pitfall 自動強制 hook 2個（`pitfall_lint` PostToolUse=警告のみ / `pitfall_commit_gate` PreToolUse Bash=danger を exit 2 ブロック）を追加 — `enable` 登録済み pitfalls.md にのみ反応するオプトイン方式（[ADR-027](../docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md)）。audit は未登録だが育っている（エントリ3+件）pitfalls.md を `Unmanaged Pitfalls` セクションで可視化し enable へ誘導（`pitfall_registry.unmanaged_candidates` + `build_unmanaged_pitfalls_section`、liveness 判定は `parse.count_entries`、glossary 同様 evolve のたびに surface ＝ install≠enforcement の可視化）。observability 行（glossary_drift / calibration_drift / outcome_metrics / promotion_readiness / weak_signals / measurement_bug ほか — 全一覧は `audit/observability.py` の `_OBSERVABILITY_BUILDERS` が SoT）は同 `_OBSERVABILITY_BUILDERS` を**単一ソース**とし、markdown 経路（`report.generate_report`）と構造化経路（`collect_observability` → evolve が `result["observability"]` に格納し SKILL.md Step 3.8 で必ず surface）の両方が同じリストを消費する（217KB markdown の選択読みで observability 行が埋もれて surface されない問題＝silence≠evaluated の再発を、生成側でなく出力経路の契約で塞ぐ、[ADR-028](../docs/decisions/028-observability-contract-audit-evolve.md)）。`belief_blocks`（belief_entropy ゲートの block 件数、#285）と `calibration_drift`（fitness 評価関数の score-acceptance 相関 drift、#286）も同 contract 経由で evolve のたびに surface。calibration drift は `fitness_evolution.detect_drifted_funcs` を audit section と trigger_engine（session 終了時に evolve-fitness を proactive 提案、変更は人間承認 MUST）が共有する単一ソース。`negative_transfer` は `compute_component_transfer`（#288）で各追加スキルを更新コンポーネントとみなし、隣接追加イベントで before/after を区切る isolation window（after_i = before_{i+1}）で「どの更新が既存スキルを回帰させたか」を分離帰属する（arXiv 2605.30621 ablation、単一転移点版の誤帰属を回避）。`eval_saturation`（#292、`eval_saturation.py` + `build_eval_saturation_section`）は forward-gen trigger eval の飽和兆候（positive 偏重 / 易しい negative / クエリ過少）を eval 実行なし・決定論で測り、calibration drift と同帯で surface（緑＝頑健か飽和かを判別、TASTE arXiv 2605.28556 着想）。`hook_drift`（[ADR-036](../docs/decisions/036-hook-drift-stale-pin-first.md)、`hook_drift.py` + `audit/sections_hook.py`）は gstack flow を参照する hook の陳腐化を stale_pin（`flow-chain.json` の `gstack_version` vs 実環境 `.last-setup-version` の version 突合、表記ゆれ false positive なし）で検出し同 contract 経由で evolve のたびに surface。builder はグローバル `~/.gstack` を読む環境グローバル系。dead_ref / internal_drift / follow-through 評価は YAGNI で別 issue #316-#318 に分離。`agent_team`（#326、`agent_team.py` + `audit/sections_agent.py`）は `agent_quality`（単体品質）と直交し、エージェント *間* の編成ギャップを 2 軸で決定論検出する: 役割重複（description 役割語の Jaccard、`similarity.jaccard_coefficient` SoT、閾値 0.5）/ 孤立（他エージェント本文への被参照と参照を見て入次数 0 かつ出次数 0 のみ＝ルーター・被参照専門家を除外）。`~/.claude/agents/` を読む環境グローバル系で、2 個未満は対象外・2 個以上はギャップ無しでも ✓ を残す。環境衛生検出器 8 件（#124-#131 + #155・2026-07-03〜07-06 の手動監査/dogfood 死角を検出器化）を advisory 共通枠（#115）経由で追加: artifact 衛生5（グローバル CLAUDE.md 空 / SKILL.md 欠落 dir / バックアップ残置 / skill 名跨 scope 重複・symlink wrapper 除外 / plugin と重複するグローバル hook 残骸〔#155・同一イベント×正規化 basename 一致〕、`audit/sections_artifacts.py`）+ memory 衛生3（MEMORY.md 索引孤児 / frontmatter スキーマ検証 / 旧 PJ memory 完全重複残骸=fleet 横断・削除は提案のみ、`memory_index_orphan/schema_check/dup_residue.py`）。agent-brushup には #130（tools 宣言 vs `memory:` 自動付与の乖離検出）を追加。詳細は [spec/components.md](components.md)。

スキル25個（ユーザー向け: evolve/audit/reflect/discover/prune/cleanup/implement/spec-keeper/second-opinion/agent-brushup/breakthrough/import/pitfall-curate/queue〔daily-evolve 待ち一覧の手動運用入口・#80 launchd の代替〕/tier〔モデルティア変更の対話 UX ラッパー・#193〕等。内部/deprecated: reorganize/evolve-loop-orchestrator/backfill 等（backfill は #215 で CLI 削除済み→observe hooks 自動記録 + evolve batch ingest に統合、スキルは廃止リダイレクトのみ #486）。enrich は v1.94.0 で削除済み）、共通ロジック24パッケージ（scripts/lib/ 配下、audit/discover/fleet/rl_common/tool_usage_analyzer に加え correction_semantic/utterance_archive/weak_signals/dogfood/remediation/verbosity/daily/subagent_traces/evolve_introspect 等パッケージ化済み。`pipeline_eval.py` / `skill_importer.py` / `pitfall_manager/injector.py` / `meta_quality.py` / `similarity.py` / `trigger_eval_generator.py` / `skill_triage.py` / `llm_broker.py` (claude -p 全廃のファイルベース2相基盤 — build_requests〔Phase A〕/parse_responses〔Phase C〕/parse_score/passthrough、IO-free・LLM-free、[ADR-037] Phase 1a) / `world_context.py` (世界観生成を `--emit-request`/`--save-from-response` の2相化、claude -p ゼロ) / `memory_temporal.py` (importance_score・reinforce_memory・write_importance_score・write_temporal_metadata〔valid_from/source_correction_ids の provenance 書込, #2〕) / `skill_evolve/rubric.py` (rubric_checkpoint) / `fitness_history_store.py` (DuckDB fitness 履歴 SoR、NaN guard 付き冪等 ingest) / `hypothesis_tracker.py` (VeriTrace Phase 1、仮説ツリー JSONL 永続化) / `skill_extractor/` (SIRI ① 成功軌跡採掘。trajectory_sampler が raw セッションから TrajectoryRecord 抽出 → skill_extractor が generalizability_score 付き候補を生成。`run_discover` が project スコープで発火し triage の missed_skill_opportunities へ合流、discover=evolve recurring ループに配線済 #291 [ADR-030](../docs/decisions/030-skill-extractor-discover-wiring-project-scoped.md)) / `glossary_drift.py` (CONTEXT.md 用語集 drift 検出、構造 gate + 未登録 jargon advisory) / `audit/observability.py` (observability セクションの単一ソース `_OBSERVABILITY_BUILDERS` + `collect_observability`、markdown/構造化の両経路が消費、[ADR-028](../docs/decisions/028-observability-contract-audit-evolve.md)) / `belief_entropy.py` (生成後 memory 要約の retention/drift 決定論ゲート、similarity 再利用・LLM ゼロ、#285) 追加。jaccard 数式は `similarity.jaccard_coefficient` に一本化（memory_gating/meta_quality/episodic_store/belief_entropy の4重複を統合））、bin/ コマンド24個（`evolve-gain` / `evolve-dogfood-gate` / `evolve-release-sync` / `evolve-scaffold-advisory` / `evolve-daily-install` / `evolve-daily-run` / `evolve-tier`〔モデルティア正典の一元管理 CLI、#193〕/ `evolve-loop-ablation`〔設計文脈 vs naive 生成比較の opt-in 較正 CLI、#234〕含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）、userConfig 21項目（`correction_preflight_threshold` / `error_preflight_threshold` / `skill_lr_budget` / `subagent_window_minutes` / `spec_trigger_enabled` / `idiom_autopromote_daily_cap` / `icebox_review_threshold_days` 追加）。evolve パイプラインに LR Budget gate（`skill_lr_budget` デフォルト30行）を組み込み、high-risk 変更を BLOCK（意図確認層 `intention_check` は宣言のみで未配線だったため #170 で削除）。

## Key Design Decisions カテゴリ別サマリ

全 52 件の詳細は [docs/decisions/](../docs/decisions/) を参照。SPEC.md からの移動（2026-04-24）。

- **配布・観測**: Plugin 配布 ([001](../docs/decisions/001-plugin-distribution-model.md)), hooks+JSONL ([002](../docs/decisions/002-observe-hooks-jsonl-architecture.md)), hook enrichment ([015](../docs/decisions/015-hook-agent-enrichment.md)), Plugin bin/ 移行 ([019](../docs/decisions/019-plugin-bin-directory-migration.md)), philosophy seed 配布 ([020](../docs/decisions/020-philosophy-seed-distribution.md))
- **パイプライン**: GA廃止→直接パッチ ([003](../docs/decisions/003-direct-patch-over-genetic-algorithm.md)), 全レイヤー診断/Compile ([007](../docs/decisions/007-all-layer-diagnose-adapter-pattern.md), [008](../docs/decisions/008-all-layer-compile-dispatch-pattern.md)), 3ステージ簡素化 ([009](../docs/decisions/009-simplify-pipeline-3-stage.md)), スキル自己進化 ([016](../docs/decisions/016-skill-self-evolution-pattern.md), [017](../docs/decisions/017-evolve-skill-independent-command.md)), evolve メタ層の自己解析→issue 半自動起票 ([033](../docs/decisions/033-evolve-introspect-self-analysis-issue-filing.md)), split↔archive 相互排他 reconcile（archive 優先, [034](../docs/decisions/034-split-archive-mutual-exclusion-archive-wins.md))
- **評価・スコアリング**: Coherence 4軸 ([004](../docs/decisions/004-coherence-score-4-axes.md)), Telemetry ([005](../docs/decisions/005-telemetry-score-architecture.md)), Constitutional Judge ([006](../docs/decisions/006-constitutional-eval-llm-judge.md)), CoT除去 ([018](../docs/decisions/018-evaluate-pipeline-cot-removal.md))
- **運用・自動化**: Auto trigger ([010](../docs/decisions/010-auto-evolve-trigger-engine.md), [011](../docs/decisions/011-auto-compression-trigger.md)), Self-Evolution EWA ([012](../docs/decisions/012-self-evolution-trajectory-ewa.md)), Compaction復元 ([013](../docs/decisions/013-compaction-state-recovery.md)), CC v2適用 ([014](../docs/decisions/014-adopt-claude-code-v2-features.md))
- **安全設計**: Cleanup tmp_dir prefix safety-first default ([021](../docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))
- **アーキテクチャ拡張**: fleet 観測＋介入を同一プラグインに統合（4 本目の柱）([022](../docs/decisions/022-fleet-observation-plus-intervention.md)), PJ 横断 memory recall は keyword 決定論・vector 非採用 ([025](../docs/decisions/025-cross-pj-memory-recall-keyword-only.md))
