# SPEC.md — rl-anything

Last updated: 2026-05-27 by /spec-keeper update (2)

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
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を単一コマンドで可視化、Phase 分け実装（Phase 1: `status`）([ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)) |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・upstream 監視 |
| セカンドオピニオン | second-opinion | Claude Agent による cold-read 独立見解（codex 代替、3モード） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| セッション管理 | handover | 作業状態を構造化ノートに書き出し（ローカルファイル or GitHub Issue）、SPEC.md 同期、別セッションへ引き継ぎ |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| **成長可視化 (NFD)** | audit --growth | NFD 論文ベースの Spiral Development Model — 4フェーズ自動判定 + Lv.1-10 レベルシステム + 環境プロファイル（5 traits）+ 成長ストーリー |
| **ROI 可視化** | rl-gain (`bin/rl-gain`) | `rtk gain` 風 ASCII レポート — 推定節約時間・Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示 |
| **コミュニティスキル import** | import (`bin/rl-fleet import`) | コミュニティリポジトリからスキルをワンコマンドで取得・インストール。`owner/repo`・ローカルパス・URL に対応。scripts/ 自動実行なし、[y/N] confirm のセキュリティゲート付き |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（マージ済みブランチ / remote refs / 一時 worktree / 一時ディレクトリ / 関連 Issue close 候補 / PR Test plan 残件 / CC プロジェクト状態パージ Category 7）を候補提示→`AskUserQuestion` 個別承認→実行で安全処理。一時ディレクトリ default prefix は `rl-anything-` のみに限定 ([ADR-021](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes` / userConfig で拡張可能 |

「4本目の柱」は fleet 観測・介入としての rl-anything 拡張。per-PJ 自己進化から fleet 自己進化への昇格（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）。

Observe hooks (21個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。UserPromptSubmit に HASP-style pitfall_injector 追加（エラー閾値検知で pitfalls.md を自動 inject）。Stop hook に `auto_memory_runner` 追加（corrections → L2 memory 候補を非同期生成、LLM 1 call 上限）。
スキル24個（ユーザー向け: evolve/audit/reflect/discover/prune/cleanup/handover/implement/spec-keeper/second-opinion/agent-brushup/breakthrough/backfill/import 等。内部/deprecated: reorganize/enrich/rl-loop-orchestrator 等）、共通ロジック14パッケージ（scripts/lib/ 配下、audit/discover/fleet/rl_common 等パッケージ化済み。`pipeline_eval.py` / `skill_importer.py` / `pitfall_manager/injector.py` / `meta_quality.py` / `similarity.py` / `trigger_eval_generator.py` / `skill_triage.py` / `world_context.py` / `memory_temporal.py` (importance_score・reinforce_memory・write_importance_score) / `skill_evolve/rubric.py` (rubric_checkpoint) 追加）、bin/ コマンド16個（`rl-backfill-turn-indices` / `rl-gain` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）、userConfig 17項目（`correction_preflight_threshold` / `error_preflight_threshold` / `skill_lr_budget` 追加）。evolve パイプラインに意図確認層（`intention_check` in `regression_gate.py`）と LR Budget gate（`skill_lr_budget` デフォルト30行）を組み込み、high-risk 変更を BLOCK。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全25件。SkillOS（Frozen Executor + Trainable Curator）/ MemOS（4層メモリ結晶化）対応設計の詳細は [spec/key-design-decisions.md](spec/key-design-decisions.md) を参照。カテゴリ別要約は [spec/architecture.md](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、ADR 原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近の変更概要。完全な履歴は [CHANGELOG.md](CHANGELOG.md) を参照。

- 2026-05-27: **fix(evolve): Step 7 prune候補の個別調査・分類フロー追加** — SKILL.md Read + git log で各候補を調査し、オンデマンド型/一時目的完了型/統合済み型/日常用途・未発火型の4種別に分類してテキスト出力後に1件ずつ承認を求めるよう変更。一括bulk判断を禁止
- 2026-05-27: **fix(evolve): auto_fixable 修正内容をQ&A前に表示するよう指示を強化** — `generate_auto_fix_summaries` の proposal/rationale を明示フォーマット（ファイルパス・修正内容・理由を1件ずつ列挙）でテキスト出力してから AskUserQuestion を呼ぶよう SKILL.md に明記。proposable の「Q&A前に補足説明」pitfall ルールを auto_fixable にも適用
- 2026-05-27: **fix(audit): stale_ref・skill_quality_pattern_gap 誤検知修正** — `path_extractor.py` でインラインバッククォート内パスをマスクして AWS SSM パス等の stale_ref 誤検知を排除。`instruction_patterns.py` に `_CHECKLIST_HEADING_RE` 追加（`## 実行前チェックリスト` 等の日本語/英語見出しを checklist パターンとして認識）。`skills/evolve/SKILL.md` Step 5.5/8/9 の指示品質改善（補足説明 pitfall・Report フォーマット規則・insufficient_data 文脈説明追加）
- 2026-05-27: **feat(evolve): --confirmed-batch フラグで batch_guard_trigger 再発火を防止** — `skill_evolve_assessment()` と `run_evolve()` に `confirmed_batch: bool = False` を追加。インタラクティブ確認済みの場合に `_MAX_AUTO_SKILLS` 閾値超過でも LLM 評価を続行可能
- 2026-05-27: **refactor(evolve): ナレーションを職人一言メモスタイルへ変更（v1.70.0）** — RPG 語彙（書架・司書・歪み）を除去し、件数ベースの短文に置換。`world_context.py` / Step 0.5 世界観ロードは維持
## Current Limitations / Known Issues

- **episodic 層 (v1.61.0)** — `prune_expired()` は `find_episodic_duplicates` 内で opportunistic 呼び出し済みだが audit 統合は未実装。`--promote-episodic` は `reflect_status == "applied"` の事前検証なし（agent の shell 呼び出しスキップで昇格漏れの可能性）。Concurrent first-write conflict は未対策（単一ユーザー用途で実用上問題なし）
- **subagent token 追跡 v1.5** — `<pj_dir>/<session-uuid>/subagents/*.jsonl` の ingest 対応済み（`isSidechain=True` でマーク）。ただし subagent の token 消費は主セッションの `message.usage` にも内包されるため二重カウントが生じる可能性あり。fleet の CACHE_HIT / REUSE は合算値で表示
- Subagents レイヤー: 乱立検知（SubagentStop hook + systemMessage 警告）は実装済み。観測・測定・進化の高度化は未着手（roadmap 参照）
- CLAUDE.md レイヤーの進化は reflect 反映のみ（自動修正なし）
- openspec/specs/ は機能仕様のみ（ADR 変換対象なし）。ディレクトリはアーカイブとして残存
- audit の CLAUDE.md line_limit_violation は warning_only

## Next

- **warn 超ファイルの対応** — `workflow_checkpoint.py` (462行) / `skill_triage.py` (471行) / `layer_diagnose.py` (437行) / `audit/orchestrator.py` (430行) が warn 閾値 (500行) に近い。hard (800行) 到達時に fleet パターンで分割（`reflect_utils.py`・`agent_quality.py` は今回分割済み）
- fleet Phase 2: `bin/rl-fleet audit-all [--parallel N]` + global rules (`~/.claude/rules/*.md`) × PJ CLAUDE.md の名前衝突検出（意味的矛盾は Phase 4+）
- fleet Phase 3: `reflect-all` / `evolve-all` を dry-run default + `--apply` で実装、`rollback <ts>` + PJ 単位 opt-in マーカー必須（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）
- fleet perf 最適化: Phase 1 実測 12.9s / 7 PJ（設計目標 3s）。`growth-state-<slug>.json` 直読みキャッシュ経路を Phase 2 で検討
- `audit.py` duckdb `usage.jsonl` クエリの `Conversion Error: Malformed JSON` 根本修正（fleet が AUDIT_ERROR として surface する既存バグ）
- Subagents レイヤーの進化メカニズム（roadmap Phase 3）
- 6レイヤー全体の自律進化ループ完成（roadmap To-be）

## 長期ロードマップ

AIRA（スキル構造自動探索エンジン、設計構想段階）の詳細は [spec/roadmap.md](spec/roadmap.md) を参照。
