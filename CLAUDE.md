# rl-anything Plugin

スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化**、**fleet 観測・介入** を提供する Claude Code Plugin。

## 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン |
| フィードバック | reflect | 修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映 |
| 直接パッチ最適化 | optimize, rl-loop, generate-fitness, evolve-fitness | corrections/context → LLM 1パスパッチ → regression gate（`scripts/lib/regression_gate.py` に共通化） |
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を一覧表示。`status` / `tokens` / `test-guard status`（no-llm-in-tests / pytest-no-llm 導入状況）/ `discover` / `recall`（全 PJ memory を keyword 横断検索、決定論・LLM 非依存） |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・新規作成・削除候補 |
| セカンドオピニオン | second-opinion | Claude Agent による独立した cold-read セカンドオピニオン（codex 代替） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| 構造化実装 | implement | plan artifact → タスク分解 → 実装（single/parallel）→ 検証 → テレメトリ記録 |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（branches / worktrees / tmp dirs / Issues / Test plan 残件）を候補提示→個別承認→実行。tmp dir default prefix は `rl-anything-` のみに安全側限定 |
| ユーティリティ | feedback, update, version, backfill | フィードバック・更新・バージョン確認・初期セットアップ |

## コンポーネント

| コンポーネント | 説明 |
|----------------|------|
| Observe hooks (21個) | LLM コストゼロで使用・エラー・修正フィードバック・ワークフロー・ファイル変更を自動記録 |
| Auto Trigger | セッション終了・corrections 蓄積・ファイル変更時に evolve/audit 実行を自動提案（`trigger_engine.py`） |
| `userConfig` | CC v2.1.83 manifest.userConfig で trigger 閾値（auto_trigger/interval/cooldown 等）と cleanup スキル prefix（`cleanup_tmp_prefixes`）・slow command 閾値（`slow_threshold_ms`）・subagent 警告閾値（`subagent_warning_threshold`）・スキル数上限（`max_skill_count`=30）・correction pre-flight 閾値（`correction_preflight_threshold`=3）・error pre-flight 閾値（`error_preflight_threshold`=3）を含む 17 項目をプラグイン有効化時に設定可能 |
| `genetic-prompt-optimizer` | corrections/context ベースの LLM 1パス直接パッチで最適化 |
| `rl-loop-orchestrator` | ベースライン取得→バリエーション生成→評価→人間確認のループ統合 |
| `rl-scorer` エージェント | オーケストレーター(haiku) + 3サブエージェント並列(tech/struct=haiku, domain=sonnet)で3軸採点 |
| `skill-triage` | テレメトリ+trigger evalで CREATE/UPDATE/SPLIT/MERGE/OK の5択判定（`scripts/lib/skill_triage.py`） |
| `trigger-eval-generator` | sessions.jsonl+usage.jsonl → skill-creator互換 evals.json 自動生成（`scripts/lib/trigger_eval_generator.py`） |
| `evolve-skill` | 特定スキルに自己進化パターン（Pre-flight / pitfalls.md）をピンポイント組み込み（`assess_single_skill` + `apply_evolve_proposal`） |
| `agent-brushup` | エージェント定義の品質診断・改善提案・upstream監視（`scripts/lib/agent_quality.py`） |
| `critical-instruction-compliance` | スキル指示の遵守保証サイクル — critical行抽出+calm/directリフレーズ+違反検出+pitfall自動学習（`scripts/lib/critical_instruction_extractor.py`） |
| `second-opinion` エージェント | cold-read セカンドオピニオン（startup/builder/general 3モード）。codex 不要で Agent ツールのみで動作 |
| `growth-level` | env_score (0.0-1.0) → Lv.1-10 + 日英称号マッピング。audit がキャッシュに保存、greeting で表示（`scripts/lib/growth_level.py`） |
| `token_usage_store` | PJ 別 LLM トークン消費の DuckDB SoR — PK uuid で冪等 ingest、`token_usage.db` にスキーマ自動作成（`scripts/lib/token_usage_store.py`） |
| `token_usage_ingest` | `~/.claude/projects/<pj>/*.jsonl` の `message.usage` を walker で取り込み、days mtime filter + batch insert（`scripts/lib/token_usage_ingest.py`） |
| `token_usage_query` | TOP-N / WoW スパイク / cache hit 異常 / PJ ドリルダウン (session/model/week)。fleet status・tokens サブコマンド・audit セクションが利用（`scripts/lib/token_usage_query.py`） |
| `auto_memory_runner` | Stop hook 終了時に corrections 直近 5 件から memory 候補を非同期生成（LLM 1 call 上限）。new-file-per-entry パターンで race condition 回避。MEMORY.md は append-only index（`hooks/auto_memory_runner.py`） |
| `meta_quality` | スキル追加前の meta-skill 品質フィルタ — 再利用頻度と Jaccard 類似度で CREATE/REVIEW/SKIP を判定。`skill-triage` の CREATE 判定パスに組み込み（`scripts/lib/meta_quality.py`） |
| `constraint_decay` | セッション後半 30% ターンに集中する correction を検出して decay_rate を算出。O(N+M) pre-index・30日 mtime フィルタ。`run_discover()` に統合（`scripts/lib/discover/patterns.py`） |
| `negative_transfer` | スキル追加イベント前後の success rate delta を計測し `delta < -0.05` で負の転移フラグを付与（`scripts/lib/audit/usage.py`） |
| `subgoal_scorer` | BES 後ろ向き分解（#253）。候補を 5 サブゴール（frontmatter/trigger/correction/line_budget/slop_free）に分解し密な中間フィードバックを返す。`optimize_core.run_subgoal_scoring` がラップ、決定論・LLM 非依存（`scripts/lib/subgoal_scorer.py`） |
| `evolution_operators` | BES 前向き進化探索（#256）。crossover/mutate/select_parents(fitness-proportional)/evolve_generation の決定論演算子。rl-loop の `--evolve-search` が subgoal fitness を consume（`scripts/lib/evolution_operators.py`） |
| `memory_trace` | MemTrace 帰属診断（#254）。episodic 検索エラーを misretrieval/context_drift/corruption の3類型に分類し `event_id` 帰属。LLM/oracle 不使用、`audit/memory.py` が利用（`scripts/lib/memory_trace.py`） |
| `slop_detector` | AI slop 辞書検出（#255）。日英 10 パターンを決定論 regex で検出、`detect_slop -> SlopResult(slop_score, hits)`。constitutional に 10% ブレンド + subgoal slop_free に接続（`scripts/lib/slop_detector.py`） |

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
- 詳細仕様: [spec/](spec/)
- 設計判断の記録: [docs/decisions/](docs/decisions/)
