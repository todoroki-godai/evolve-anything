# SPEC.md — rl-anything

Last updated: 2026-05-29 by /spec-keeper update

## Overview

Claude Code Plugin。スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化** を提供する。AI がセッション中に蓄積した使用データ・エラー・修正パターンを基に、スキル/ルール/メモリ/CLAUDE.md を自律的に改善する。

対象ユーザー: Claude Code を日常的に使い、スキル/ルール環境を継続的に改善したい開発者。

## Tech Stack

- **言語**: Python 3 (hooks, scripts), Markdown (skills, rules)
- **配布**: Claude Code Plugin (`claude plugin install`)
- **テレメトリ**: JSONL ファイル (usage/errors/corrections/sessions/workflows/skill_activations.jsonl)
- **クエリ**: DuckDB (JSONL→SQL、未インストール時は Python フォールバック)
- **テスト**: pytest
- **CI**: `claude plugin validate`

## System Architecture

### 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン ([ADR-009](docs/decisions/009-simplify-pipeline-3-stage.md)) |
| フィードバック | reflect | 修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映。approve 時に episodic 層（DuckDB TTL 30d）に昇格し、次セッションで「N日前に対処済み」として重複修正を検出 |
| 直接パッチ最適化 | rl-loop, generate-fitness, evolve-fitness | GA廃止、LLM 1パス直接パッチ ([ADR-003](docs/decisions/003-direct-patch-over-genetic-algorithm.md)) → regression gate。optimize は CLI/内部呼び出し専用（`bin/rl-optimize`、rl-loop から起動） |
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を単一コマンドで可視化、Phase 分け実装（Phase 1: `status`）([ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md))。`recall` で全 PJ memory を keyword 決定論横断検索（LLM/embedding 非依存、[ADR-025](docs/decisions/025-cross-pj-memory-recall-keyword-only.md)） |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・upstream 監視 |
| セカンドオピニオン | second-opinion | Claude Agent による cold-read 独立見解（codex 代替、3モード） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| セッション管理 | handover | 作業状態を構造化ノートに書き出し（ローカルファイル or GitHub Issue）、SPEC.md 同期、別セッションへ引き継ぎ |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| **成長可視化 (NFD)** | audit --growth | NFD 論文ベースの Spiral Development Model — 4フェーズ自動判定 + Lv.1-10 レベルシステム + 環境プロファイル（5 traits）+ 成長ストーリー |
| **ROI 可視化** | rl-gain (`bin/rl-gain`) | `rtk gain` 風 ASCII レポート — 推定節約時間・Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示 |
| **コミュニティスキル import** | import (`bin/rl-fleet import`) | コミュニティリポジトリからスキルをワンコマンドで取得・インストール。`owner/repo`・ローカルパス・URL に対応。scripts/ 自動実行なし、[y/N] confirm のセキュリティゲート付き |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（マージ済みブランチ / remote refs / 一時 worktree / 一時ディレクトリ / 関連 Issue close 候補 / PR Test plan 残件 / CC プロジェクト状態パージ Category 7）を候補提示→`AskUserQuestion` 個別承認→実行で安全処理。一時ディレクトリ default prefix は `rl-anything-` のみに限定 ([ADR-021](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes` / userConfig で拡張可能 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存スキル。`seed`（正準ひな型生成）/ `normalize`（既存ファイルを正準形へ冪等変換）/ dedup（jaccard、日本語は CJK bigram、Root-cause 不在時は本文 fallback）/ 普遍性分類（`Transferability` universal/project/instance + `Generality` 1-5）/ 配布版(Top-N) / 同期ゲート。パーサは正準・`## N.`番号付き・インラインパイプ・`<!-- -->`スキップに対応（収束路線、[ADR-027](docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md)）。判断は agent、決定論処理は `scripts/core.py`（curate）+ `parse.py`（フォーマット I/O）。`similarity.py` 再利用。`pitfall_manager`（自己進化専用）とは別ライフサイクルで共存（[ADR-026](docs/decisions/026-pitfall-curate-vs-pitfall-manager.md)） |

「4本目の柱」は fleet 観測・介入としての rl-anything 拡張。per-PJ 自己進化から fleet 自己進化への昇格（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）。

Observe hooks (21個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。UserPromptSubmit に HASP-style pitfall_injector 追加（エラー閾値検知で pitfalls.md を自動 inject）。Stop hook に `auto_memory_runner` 追加（corrections → L2 memory 候補を非同期生成、LLM 1 call 上限）。pitfall 自動強制 hook 2個（`pitfall_lint` PostToolUse=警告のみ / `pitfall_commit_gate` PreToolUse Bash=danger を exit 2 ブロック）を追加 — `enable` 登録済み pitfalls.md にのみ反応するオプトイン方式（[ADR-027](docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md)）。
スキル25個（ユーザー向け: evolve/audit/reflect/discover/prune/cleanup/handover/implement/spec-keeper/second-opinion/agent-brushup/breakthrough/backfill/import/pitfall-curate 等。内部/deprecated: reorganize/enrich/rl-loop-orchestrator 等）、共通ロジック14パッケージ（scripts/lib/ 配下、audit/discover/fleet/rl_common 等パッケージ化済み。`pipeline_eval.py` / `skill_importer.py` / `pitfall_manager/injector.py` / `meta_quality.py` / `similarity.py` / `trigger_eval_generator.py` / `skill_triage.py` / `world_context.py` / `memory_temporal.py` (importance_score・reinforce_memory・write_importance_score) / `skill_evolve/rubric.py` (rubric_checkpoint) / `fitness_history_store.py` (DuckDB fitness 履歴 SoR、NaN guard 付き冪等 ingest) / `hypothesis_tracker.py` (VeriTrace Phase 1、仮説ツリー JSONL 永続化) / `skill_extractor/trajectory_sampler.py` (raw セッションから TrajectoryRecord 抽出、ストリーミング読み込み) 追加）、bin/ コマンド16個（`rl-backfill-turn-indices` / `rl-gain` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）、userConfig 17項目（`correction_preflight_threshold` / `error_preflight_threshold` / `skill_lr_budget` 追加）。evolve パイプラインに意図確認層（`intention_check` in `regression_gate.py`）と LR Budget gate（`skill_lr_budget` デフォルト30行）を組み込み、high-risk 変更を BLOCK。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全28件（最新: [ADR-027](docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md) pitfall-curate はフォーマット収束路線 — 寛容パーサでなく seed+normalize で正準形へ寄せる）。SkillOS（Frozen Executor + Trainable Curator）/ MemOS（4層メモリ結晶化）対応設計の詳細は [spec/key-design-decisions.md](spec/key-design-decisions.md) を参照。カテゴリ別要約は [spec/architecture.md](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、ADR 原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近の変更概要（完全な履歴は [CHANGELOG.md](CHANGELOG.md)）:
- 2026-05-29: **feat(pitfall): pitfalls.md 自動強制フロー — install + enable で以後ルールが当たる (#265)** — agent が手編集して後 curate すると壊れる問題への恒久対策。`normalize --check`（書き換えず ok/drift/danger 判定、exit 0/1/2）を土台に、編集時 hook `pitfall_lint`（警告のみ非ブロッキング）+ commit 時ゲート `pitfall_commit_gate`（danger は exit 2 ブロック・drift は警告）の二段検査。自動書き換えはしない（silent wipe の反省）。`enable`/`disable` で `.claude/rl-anything/pitfall-managed.json` に登録するオプトイン方式（`pitfall_registry.py`、決定論・LLM非依存）。実 git E2E で ok→通過 / drift→警告 / danger→ブロックを確認。各 PJ で `enable` を1回叩けば以後自動。[ADR-027]
- 2026-05-29: **feat(pitfall): `pitfall-curate` スキル新設 + 多フォーマット対応（収束路線）** — figma 由来の pitfall 運用の型を脱ドメイン化。決定論コアを `core.py`（curate: dedup/classify/distill/sync）+ `parse.py`（フォーマット I/O: parse/seed/normalize）に分離、agent が分類・reframing 判断、script は LLM 非依存。atlas-browser/sys-bots/docs-platform 実機ドッグフードでフォーマット断片化を発見し、パーサを正準・`## N.`番号付き・インラインパイプ・`<!-- -->`スキップ対応に拡張、日本語 dedup は CJK bigram + 本文 fallback。`seed`/`normalize` で新規導入・既存ファイルを正準形へ収束。`similarity.py` 再利用、`pitfall_manager` とは別ライフサイクル
- 2026-05-28: **feat: BES サブゴールスコアラー / 前向き進化探索 / MemTrace / slop 辞書 (#253-#256)** — tech-eval 由来。`subgoal_scorer.py`（5サブゴール密フィードバック）+ `evolution_operators.py`（rl-loop `--evolve-search` の crossover/mutate）+ `memory_trace.py`（検索エラー3類型帰属）+ `slop_detector.py`（日英10パターン、constitutional 10%ブレンド・subgoal slop_free 接続）。全て決定論・LLM 非依存
- 2026-05-27: **feat(tech-eval): fitness_history_store / hypothesis_tracker / trajectory_sampler / memory-gating 追加 (#238-#241)** — `fitness_history_store.py`（DuckDB 冪等 ingest、environment fitness を自動記録）、`hypothesis_tracker.py`（VeriTrace Phase 1、仮説ツリー JSONL 永続化・confidence 更新・矛盾検出）、`skill_extractor/trajectory_sampler.py`（raw セッション JSONL から TrajectoryRecord 抽出）、`auto_memory_runner` に memory-gating（類似度スコアリング）を追加
- 2026-05-27: **fix(fitness-history-store): DuckDB 構文・NaN guard・テスト品質改善 (#249)** — `INSERT OR IGNORE` → `INSERT INTO ... ON CONFLICT DO NOTHING`、`math.isfinite` NaN ガード、`_load_sibling` を coherence パッケージ（`__init__.py` 付きディレクトリ）対応に拡張、test call_args アサーション追加・env var end-to-end テスト追加

## Current Limitations / Known Issues

詳細は [spec/limitations.md](spec/limitations.md) を参照。主な制限: episodic 層 audit 未統合、subagent token 二重カウント可能性、CLAUDE.md レイヤーは reflect 反映のみ。

## Next

近期の作業項目（warn 超ファイル分割、fleet Phase 2/3、perf、既知バグ、Subagents 進化等）は [spec/next.md](spec/next.md) を参照。

## 長期ロードマップ
AIRA（スキル構造自動探索エンジン、設計構想段階）の詳細は [spec/roadmap.md](spec/roadmap.md) を参照。
