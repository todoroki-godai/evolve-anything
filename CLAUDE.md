# rl-anything Plugin

スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化**、**fleet 観測・介入** を提供する Claude Code Plugin。

## 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン |
| フィードバック | reflect | 修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映 |
| 直接パッチ最適化 | optimize, rl-loop, generate-fitness, evolve-fitness | corrections/context → LLM 1パスパッチ → regression gate（`scripts/lib/regression_gate.py` に共通化） |
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を一覧表示。`status` / `tokens` / `test-guard status`（no-llm-in-tests / pytest-no-llm 導入状況）/ `discover` / `recall`（全 PJ memory を keyword 横断検索、決定論・LLM 非依存）/ `plugins`（インストール済み CC プラグインの最新性診断 — update/drift/unknown を決定論検出。version 無しプラグインの silent stale を cache↔marketplace source の差分で検出） |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・新規作成・削除候補 |
| セカンドオピニオン | second-opinion | Claude Agent による独立した cold-read セカンドオピニオン（codex 代替） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| 構造化実装 | implement | plan artifact → タスク分解 → 実装（single/parallel）→ 検証 → テレメトリ記録 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存ツール。類似 dedup / 普遍性分類（universal/project/instance + 汎用度1-5）/ 三段階開示の配布版(Top-N)生成 / 記録↔分類↔配布の同期ゲート。判断は agent、決定論処理は `scripts/pitfall_curate.py`。`pitfall_manager`（自己進化専用）とは別物 |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（branches / worktrees / tmp dirs / Issues / Test plan 残件）を候補提示→個別承認→実行。tmp dir default prefix は `rl-anything-` のみに安全側限定 |
| ユーティリティ | feedback, update, version, backfill | フィードバック・更新・バージョン確認・初期セットアップ |

## コンポーネント

各コンポーネントの設計経緯・根拠・issue/ADR 参照を含む詳細は **[spec/components.md](spec/components.md)**（SoT）。
ここは 1 行サマリのみ。**新コンポーネント追加・変更時は spec/components.md に詳細を書き、この表には 1 行だけ追記する。**

| コンポーネント | 一言サマリ | 実体 |
|----------------|-----------|------|
| Observe hooks (20個) | LLM コストゼロで使用・エラー・修正・ワークフロー・ファイル変更を自動記録 | `hooks/` |
| Auto Trigger | corrections 蓄積・セッション終了等で evolve/audit を自動提案 | `trigger_engine.py` |
| `userConfig` | trigger 閾値・各種上限など 18 項目をプラグイン有効化時に設定可能 | manifest |
| `genetic-prompt-optimizer` | corrections/context ベースの LLM 1パス直接パッチ | agent |
| `rl-loop-orchestrator` | ベースライン→バリエーション→評価→人間確認のループ統合 | agent |
| `rl-scorer` | オーケストレーター + 3並列サブエージェントで3軸採点 | agent |
| `skill-triage` | CREATE/UPDATE/SPLIT/MERGE/OK の5択判定 | `skill_triage.py` |
| `trigger-eval-generator` | sessions+usage → skill-creator 互換 evals.json 自動生成 | `trigger_eval_generator.py` |
| `evolve-skill` | 自己進化パターン（Pre-flight / pitfalls.md）のピンポイント組み込み | skill |
| `agent-brushup` | エージェント定義の品質診断・改善提案・upstream 監視 | `agent_quality.py` |
| `critical-instruction-compliance` | critical 行抽出+リフレーズ+違反検出+pitfall 自動学習 | `critical_instruction_extractor.py` |
| `second-opinion` | cold-read セカンドオピニオン（3モード、codex 不要） | agent |
| `growth-level` | env_score → Lv.1-10 + 日英称号マッピング | `growth_level.py` |
| `optimize_history_store` | accept/reject 履歴の正準ストア（PJ スコープ・worktree 安全 slug）[ADR-031] | `optimize_history_store.py` |
| `evolve_decisions` | evolve 提案の accept/reject を emit→drain 2相で決定論キャプチャ（`rl-evolve --drain`）[ADR-041, #400, #402] | `evolve_decisions.py` |
| `evolve_reconcile` | skill_evolve↔archive 矛盾の reconcile + batch_skip の observability 昇格（#400） | `evolve_reconcile.py` |
| `token_usage_store/ingest/query` | PJ 別 LLM トークン消費の DuckDB SoR / 取り込み / 集計 | `token_usage_*.py` |
| `auto_memory_runner/broker` | auto-memory の enqueue（ゼロ LLM）+ 2相生成・書込 [ADR-037] | `auto_memory_*.py` |
| `meta_quality` | スキル追加前の品質フィルタ（CREATE/REVIEW/SKIP） | `meta_quality.py` |
| `triage_ledger` | SKIP 判断の状態管理（TTL 45日・再発昇格・dry-run 非書込）（#308） | `triage_ledger.py` |
| `constraint_decay` | セッション後半に集中する correction の decay 検出 | `discover/patterns.py` |
| `negative_transfer` | スキル追加前後の success delta 計測 + 更新コンポーネント別帰属（#288） | `audit/usage.py` |
| `eval_saturation` | trigger eval の飽和兆候診断（#292） | `eval_saturation.py` |
| `subgoal_scorer` | BES 後ろ向き分解 — 5 サブゴール中間フィードバック（#253） | `subgoal_scorer.py` |
| `evolution_operators` | BES 前向き進化探索の決定論演算子（#256） | `evolution_operators.py` |
| `memory_trace` | episodic 検索エラーの3類型帰属（#254） | `memory_trace.py` |
| `slop_detector` | AI slop 日英 10 パターンの決定論検出（#255） | `slop_detector.py` |
| `skill_extractor` | 成功軌跡採掘→スキル候補生成 + 4軸分解 + 3層ノイズ除去（#291, #381, #387） | `skill_extractor/` |
| `skill_rm` | スキル軸の異種基準統一報酬 — 3軸射影で横断評価（#304） | `fitness/skill_rm.py` |
| pitfall 自動強制 | pitfalls.md の編集時 lint + commit ゲート（オプトイン）[ADR-027] | `pitfall_registry.py` |
| `agent_team` | エージェント間の役割重複・孤立の決定論検出（#326） | `agent_team.py` |
| observability contract | 必ず surface すべき行の単一ソース（markdown/構造化 両経路）[ADR-028] | `audit/observability.py` |
| `evolve_introspect` | evolve result の自己解析→issue 候補生成（3カテゴリ）[ADR-033, ADR-034] | `evolve_introspect.py` |
| `evolve_result_schema` | result JSON の正準スキーマ契約 — impl/doc 両 drift 検出（#375, #379） | `evolve_result_schema.py` |
| `evolve_consistency` | P1 invariant の runtime self-detect（型 drift のみ）（#377-5） | `evolve_consistency.py` |
| `hook_drift` | 他ツール追従 hook の陳腐化検出（stale_pin）[ADR-036] | `hook_drift.py` |
| `data_dir_migration` | DATA_DIR hook/tool 分裂の一元化 migration（marker ゲート redirect + DuckDB rebuild マージ、`rl-fleet migrate-data`）[#364, ADR-042] | `data_dir_migration.py` |
| `spec_trigger` | 仕様未更新マージの SessionStart 検出→spec-keeper 提案 [ADR-044] | `spec_trigger.py` |
| `capture_rate` | correction capture 率（20+ ターン session のうち correction 検出割合）を決定論算出し audit に advisory surface（#421） | `capture_rate.py` |
| `orphan_store` | writer あり reader なしの jsonl ストアを決定論検出（hooks=writer / scripts+skills=reader 静的突合）（#422） | `orphan_store.py` |
| `outcome_metrics` | 行動アウトカム3軸（correction 再発率 / 一発成功率 / rework 率近似）を advisory 表示。utilization の plugin レイアウト探索修理も同梱 [#423, ADR-046] | `audit/outcome_metrics.py` |
| `outcome_attribution` | outcome 2軸（一発成功率 / rework 率）を per-skill 帰属し evolve ターゲットランキングへ自動入力（advisory→閉ループの先行配線）。dry-run に before/after 順位差分を surface [#433] | `audit/outcome_attribution.py` |

## クイックスタート

```
# 初回セットアップ（新規PJ導入時）
/rl-anything:backfill           # 既存セッション履歴をバックフィル → 分析レポート

# 日次運用（全フェーズ一括）
/rl-anything:evolve

# 修正フィードバックの反映
/rl-anything:reflect

# 特定スキルの自己進化パターン組み込み
/rl-anything:evolve-skill my-skill

# 環境の健康診断
/rl-anything:audit

# 全 PJ 横断の fleet ステータス
bin/rl-fleet status

# PJ 別 LLM トークン消費の初期取り込み（直近 90 日）
bin/rl-fleet tokens --backfill

# PJ 別 LLM トークン消費サマリ (TOP 3 + 異常)
bin/rl-fleet tokens

# 全 PJ の memory を keyword 横断検索（決定論・LLM 非依存）
bin/rl-fleet recall "duckdb checkpoint"
bin/rl-fleet recall "認証 ルーティング" --json --limit 5

# インストール済み CC プラグインの最新性診断（update/drift/unknown を決定論検出）
bin/rl-fleet plugins
bin/rl-fleet plugins --json

# エージェント品質診断
/rl-anything:agent-brushup

# セカンドオピニオン（codex代替）
/rl-anything:second-opinion

# SPEC.md の初期化・更新
/rl-anything:spec-keeper init
/rl-anything:spec-keeper update

# 孤立した依存プラグインのクリーンアップ
claude plugin prune
```

## 適応度関数

組み込み8個: `default`（LLM汎用評価）、`skill_quality`（ルールベース構造品質）、`coherence`（構造的整合性4軸）、`telemetry`（テレメトリ3軸）、`constitutional`（原則ベースLLM Judge評価 + /cso security軸）、`chaos`（仮想除去ロバストネス）、`environment`（coherence+telemetry+constitutional+skill_quality 動的重み統合、`config.py` で閾値集約）、`plugin`（rl-anything 用プラグイン統合 fitness）。
プロジェクト固有: `scripts/rl/fitness/{name}.py` に配置 → `--fitness {name}` で使用。
環境スコア: `audit --coherence-score --telemetry-score --constitutional-score` で構造品質+行動実績+原則遵守の統合スコアを表示。

詳細は [README.ja.md](README.ja.md#適応度関数) を参照。

## rl-scorer のドメイン自動判定

CLAUDE.md からドメイン（ゲーム/API/Bot/ドキュメント）を推定し評価軸を自動切替。
詳細は [README.ja.md](README.ja.md#rl-scorer-のドメイン自動判定) を参照。

## Superpowers 共存

Superpowers プラグインがインストールされている場合、メタ操作時（evolve/audit/reflect/optimize/discover）は Superpowers の TDD/SDD/debugging スキルを発火させない。開発タスク時はフル活用する。

## Compaction Instructions

コンテキスト圧縮時、以下の情報をサマリーに必ず含めること:

1. **完了済みタスクと未完了タスクの区別** — 完了タスクを再実行しないこと
2. **呼び出されたスキルの実行結果** — 完了/未完了/エラーの状態
3. **変更したファイルの一覧** — パスと変更内容の要約
4. **ユーザーの最後の指示** — 次に何をすべきかの文脈

## テスト

```bash
cd <PLUGIN_DIR>
python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v

# プラグイン定義の整合性チェック
claude plugin validate
```

## Specification
- 現在の仕様全体像: [SPEC.md](SPEC.md)
- コンポーネント詳細（設計経緯・issue/ADR 参照の SoT）: [spec/components.md](spec/components.md)
- 用語集（Ubiquitous Language）: [CONTEXT.md](CONTEXT.md) — PJ 固有 jargon を 1 語で decode。鮮度は `scripts/lib/glossary_drift.py` が検出し spec-keeper update が advisory 提示。新概念を入れたら CONTEXT.md に 1 行追記する
- 詳細仕様: [spec/](spec/)
- 設計判断の記録: [docs/decisions/](docs/decisions/)
