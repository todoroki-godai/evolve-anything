# SPEC.md — rl-anything

Last updated: 2026-05-26 by /spec-keeper update (v1.68.0)

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
スキル24個（ユーザー向け: evolve/audit/reflect/discover/prune/cleanup/handover/implement/spec-keeper/second-opinion/agent-brushup/breakthrough/backfill/import 等。内部/deprecated: reorganize/enrich/rl-loop-orchestrator 等）、共通ロジック14パッケージ（scripts/lib/ 配下、audit/discover/fleet/rl_common 等パッケージ化済み。`pipeline_eval.py` / `skill_importer.py` / `pitfall_manager/injector.py` / `meta_quality.py` / `similarity.py` / `trigger_eval_generator.py` / `skill_triage.py` 追加）、bin/ コマンド16個（`rl-backfill-turn-indices` / `rl-gain` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）、userConfig 17項目（`correction_preflight_threshold` / `error_preflight_threshold` / `skill_lr_budget` 追加）。evolve パイプラインに意図確認層（`intention_check` in `regression_gate.py`）と LR Budget gate（`skill_lr_budget` デフォルト30行）を組み込み、high-risk 変更を BLOCK。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全24件。SkillOS（Frozen Executor + Trainable Curator）/ MemOS（4層メモリ結晶化）対応設計の詳細は [spec/key-design-decisions.md](spec/key-design-decisions.md) を参照。カテゴリ別要約は [spec/architecture.md](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、ADR 原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近の変更概要。完全な履歴は [CHANGELOG.md](CHANGELOG.md) を参照。

- 2026-05-26: **feat(evolve/fitness): batch guard 永続 denylist + evolve diff 採点蓄積 + shim 修正 v1.68.0 (#225 #223 #227 #229)** — batch guard を all-or-nothing RuntimeError からグループ単位スキップ + 永続 denylist (`skill-evolve-denylist.json`) に置換 (#225/#230)。evolve の skill diff accept/reject を `evaluate_skill_quality` で採点し `fitness_func=skill_quality` / `source=evolve_remediation` で history.jsonl に冪等蓄積、`analyze_correlations` を fitness_func グループ化 (#223/#228)。discover/prune shim の `spec_from_file_location` 化で test 収集の RecursionError / FileNotFoundError を修正 (#227 #229)
- 2026-05-26: **feat(evolve): 提案詳細プロトコル + missing_effort type 不一致修正 v1.67.0 (#225 #226)** — evolve の AskUserQuestion 提案が「N件を追加しますか？」のように件数だけで判断材料が薄い問題に対応。SKILL.md に共通の「提案詳細プロトコル」を新設し、提案前に各対象を per-item 展開（対象/根拠=detail 実値/変更内容 before→after）するよう統一。`generate_proposals()`/`generate_rationale()` に `missing_effort` 分岐を追加。あわせて検出側 LIVE type `missing_effort` と定数 `MISSING_EFFORT_CANDIDATE`（旧 `missing_effort_candidate`）の不一致で effort 追加が no-op だったバグを修正、回帰テスト追加
- 2026-05-25: **feat(backfill): constraint_decay 用 turn_index backfill v1.65.0 (#214)** — `bin/rl-backfill-turn-indices` + `backfill_turn_indices.py` を追加（`/rl-anything:backfill` スキルとは別物）。sessions.jsonl に `max_turn_index`、corrections.jsonl に `turn_index` を一度きり backfill。constraint_decay が実機で動作することを確認（WARNING 5件検出）
- 2026-05-25: **feat(hooks): auto_memory_runner + Stop hook L2 memory v1.64.0 (#198 #204)** — Stop hook 終了時に corrections 直近5件から memory 候補を非同期生成（LLM 1 call 上限）。`new-file-per-entry` パターンで race condition 回避。`adr-memory-frontmatter-v2.md` でメモリ frontmatter v2 仕様を定義。テスト +24件
- 2026-05-25: **feat(triage): meta-skill 品質フィルタ v1.64.0 (#203)** — `meta_quality.py` が skill_triage CREATE 判定パスに統合。再利用頻度と Jaccard 類似度で CREATE/REVIEW/SKIP を判定しスキルバブル防止。`similarity.py` / `trigger_eval_generator.py` 追加。fix(triage): `session_count=0` バグ修正（v1.64.1）で reuse_rate 固定を解消

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
