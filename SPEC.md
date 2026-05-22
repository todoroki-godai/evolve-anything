# SPEC.md — rl-anything

Last updated: 2026-05-22 by /spec-keeper update (feat/issue-194-agentAtlas-insights-mempi)

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

Observe hooks (20個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。UserPromptSubmit に HASP-style pitfall_injector 追加（エラー閾値検知で pitfalls.md を自動 inject）。
ユーザー向けスキル20個 + 内部スキル（reorganize / enrich deprecated）、共通ロジック14パッケージ・157モジュール（scripts/lib/ 配下、audit/discover/fleet/rl_common 等パッケージ化済み。`pipeline_eval.py` / `skill_importer.py` / `pitfall_manager/injector.py` 追加）、bin/ コマンド18個（`rl-gain` / `rl-score-notify` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）、userConfig 15項目（`error_preflight_threshold` 追加）。evolve パイプラインに意図確認層（`intention_check` in `regression_gate.py`）を組み込み、high-risk 変更を BLOCK。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全24件。カテゴリ別要約は [spec/architecture.md#key-design-decisions-カテゴリ別サマリ](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、原文は [docs/decisions/](docs/decisions/) を参照。

### Frozen Executor + Trainable Curator（SkillOS 設計との同型性）

rl-anything は **Claude Code を frozen executor**、**plugin 層を trainable curator** として
分離する設計を採用する（[ADR-023](docs/decisions/023-skillos-frozen-executor-trainable-curator.md)）。この設計は SkillOS 論文（Ouyang et al., 2026, arXiv:2605.06614）
が独立に実証した同型アーキテクチャと一致する。

SkillOS の報酬設計から取り込んだ要素:
- **r^comp**: skill 数 / invocation 数 による圧縮ペナルティ（skill バブル防止）
- **r^fc**: skill 別エラー率から推定する valid tool call 率

rl-anything の優位点（SkillOS 対比）:
- skill_triage の 5 択（SPLIT/MERGE を含む）vs SkillOS の 3 操作
- regression gate（`scripts/lib/regression_gate.py`）による safety 層

詳細: docs/research/skillos-tech-eval.md / [ADR-023](docs/decisions/023-skillos-frozen-executor-trainable-curator.md)

### 4層メモリ結晶化（MemOS 対応設計）

rl-anything の corrections→evolve パイプラインは MemOS / HiMem（arXiv:2601.06377）の
L1→L4 結晶化アーキテクチャと同型の設計を採用する（[ADR-024](docs/decisions/024-memory-crystallization-memos-correspondence.md)）。

| MemOS 層 | rl-anything 対応 |
|---------|-----------------|
| L1 トレース | `corrections.jsonl` / `sessions.jsonl` 等（Observe hooks が記録） |
| **Episodic 層** | `episodic.db`（DuckDB TTL 30d、`/reflect` approve で昇格。`episodic_store.py` / `episodic_retriever.py`）— L1 と L2 の橋渡し。クロスセッション短期記憶 |
| L2 ポリシー | `MEMORY.md` (auto-memory、`/reflect` で更新) |
| L3 ワールドモデル | `rules/*.md` + `CLAUDE.md`（`/evolve` で昇格） |
| L4 結晶化スキル | `.claude/skills/*.md`（`skill_triage` / `/evolve-skill` で生成） |

**ギャップマッピング（将来検討）**:

- **未実装: 層間矛盾検出** — L2（MEMORY.md）と L3（rules）の矛盾エントリを自動検出する仕組みがない
- **未実装: 自動 reconsolidation** — MemOS が定義する下向き伝播（上位層変更が下位層を更新）も未実装
- **未実装: ハイブリッド検索** — MEMORY.md は現状線形スキャン。MemOS/HiMem が提案する
  ベクトル検索 + 構造検索のハイブリッドは未実装
- **参照**: MemOS/HiMem (Zhang et al., 2026, arXiv:2601.06377)、[ADR-024](docs/decisions/024-memory-crystallization-memos-correspondence.md)

## Recent Changes

直近の変更概要。完全な履歴は [CHANGELOG.md](CHANGELOG.md) を参照。

- 2026-05-22: **feat(telemetry/audit/reflect): AgentAtlas / Insights Generator / Mem-π v1.63.0 (#194)** — ①`error_category` フィールドを corrections.jsonl に hook が自動付与（LLMコストゼロ分類: behavioral/guardrail/explicit/unknown）。②新モジュール `corrections_insights.py` で繰り返し失敗パターン TOP-N を集計し `/audit` レポートに自動表示（件数閾値10件）。③`score_failure_distribution()` を telemetry.py に追加し `compute_telemetry_score` の戻り値に `failure_distribution` キーを付与。④`calculate_importance_score()` を reflect.py に追加（Mem-π: `confidence × max(0, 1 - elapsed_days / decay_days)`、clamp [0.0, 1.0]）。⑤evolve-skill pre-flight に冪等性チェック追記（12-factor-agents Factor 5-6）。テスト +39件
- 2026-05-21: **feat(implement): depends_on グラフと Ready tasks 検出** — `/implement` スキルに beads インスパイアのタスク依存グラフを追加。「依存」列を task # 列記に formal 化、topological sort で循環依存を検出（ERR で停止）、Ralph Loop 開始前に Ready/Blocked 一覧を表示、各タスク前に depends_on チェックを実施。Parallel モードはクロスレーン依存を検出して Standard に自動デグレード。テレメトリ: `tasks_completed → list[str]` + `tasks_count(int)` に変更
- 2026-05-21: **feat(memory): 階層型クロスセッションメモリ v1.61.0 (#189)** — 同じ修正が繰り返されなくなる。`reflect` approve 時に `episodic.db`（DuckDB TTL 30d）に昇格し、次セッションで「N日前に対処済み」として surface。`episodic_store.py` / `episodic_retriever.py` 追加。`rl-reflect --promote-episodic` CLI で手動昇格も可能。471 tests passed
- 2026-05-21: **feat(pitfall-inject): HASP-style 失敗状態検知 pitfall inject v1.60.0 (#188)** — セッション内エラーが `error_preflight_threshold`(デフォルト3) に達した時、`last_skill` の `pitfalls.md` Active セクションを UserPromptSubmit で自動 inject。`hooks/pitfall_injector.py` + `pitfall_manager/injector.py` 追加。重複防止 (`/tmp/rl-anything-injected-{session_id}.json`)。inject 遅延1ターンは CC API 制約（TODOS.md P3）。テスト +27件
- 2026-05-21: **feat(lifecycle): スキルライフサイクル管理の強化 v1.59.0 (#186)** — Library Drift (arXiv:2605.19576) / HASP (arXiv:2605.17734) 対応。① `observe.py` が `outcome`(success/error) を `usage.jsonl` に記録、`aggregate_contribution_scores` でスキル別貢献スコアを集計し audit レポートに表示。② `detect_retirement_candidates` が低貢献スキルをアーカイブ候補として検出（クロスプロジェクトスコープ）。③ `max_skill_count`(30) を userConfig に追加し audit Summary に「スキル数/推奨上限」を表示。④ `correction_preflight_threshold`(3) を userConfig に追加し `evaluate_corrections` でスキル単位の correction 集中時に `/evolve-skill` 提案を自動出力。テスト 1807件（+7件）

## Current Limitations / Known Issues

- **episodic 層 (v1.61.0)** — `prune_expired()` は `find_episodic_duplicates` 内で opportunistic 呼び出し済みだが audit 統合は未実装。`--promote-episodic` は `reflect_status == "applied"` の事前検証なし（agent の shell 呼び出しスキップで昇格漏れの可能性）。Concurrent first-write conflict は未対策（単一ユーザー用途で実用上問題なし）
- **subagent token 追跡 v1.5** — `<pj_dir>/<session-uuid>/subagents/*.jsonl` の ingest 対応済み（`isSidechain=True` でマーク）。ただし subagent の token 消費は主セッションの `message.usage` にも内包されるため二重カウントが生じる可能性あり。fleet の CACHE_HIT / REUSE は合算値で表示
- Subagents レイヤー: 乱立検知（SubagentStop hook + systemMessage 警告）は実装済み。観測・測定・進化の高度化は未着手（roadmap 参照）
- CLAUDE.md レイヤーの進化は reflect 反映のみ（自動修正なし）
- openspec/specs/ は機能仕様のみ（ADR 変換対象なし）。ディレクトリはアーカイブとして残存
- audit の CLAUDE.md line_limit_violation は warning_only

## Next

- **warn 超ファイルの対応** — `workflow_checkpoint.py` (462行) / `skill_triage.py` (458行) / `layer_diagnose.py` (433行) / `audit/orchestrator.py` (420行) が warn 閾値 (500行) に近い。hard (800行) 到達時に fleet パターンで分割（`reflect_utils.py`・`agent_quality.py` は今回分割済み）
- fleet Phase 2: `bin/rl-fleet audit-all [--parallel N]` + global rules (`~/.claude/rules/*.md`) × PJ CLAUDE.md の名前衝突検出（意味的矛盾は Phase 4+）
- fleet Phase 3: `reflect-all` / `evolve-all` を dry-run default + `--apply` で実装、`rollback <ts>` + PJ 単位 opt-in マーカー必須（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）
- fleet perf 最適化: Phase 1 実測 12.9s / 7 PJ（設計目標 3s）。`growth-state-<slug>.json` 直読みキャッシュ経路を Phase 2 で検討
- `audit.py` duckdb `usage.jsonl` クエリの `Conversion Error: Malformed JSON` 根本修正（fleet が AUDIT_ERROR として surface する既存バグ）
- Subagents レイヤーの進化メカニズム（roadmap Phase 3）
- 6レイヤー全体の自律進化ループ完成（roadmap To-be）

## 長期ロードマップ: AIRA（スキル構造自動探索エンジン）

> **ステータス**: 設計構想段階。実装は論文コード公開後に検討。

### 概要

AIRA（Automated Instruction Refinement and Architecture）は、スキル定義の構造そのものを
自律的に探索・最適化するエンジンの構想。現在の rl-anything がスキルの「内容」を最適化するのに対し、
AIRA はスキルの「形式・構造・発火条件」まで含めた探索を行う。

### 設計構想

- **構造探索**: スキルのセクション構成・例示密度・制約の書き方を変数として最適化
- **発火条件最適化**: description とユーザー発話のマッチング精度を定量評価
- **クロスPJ転移**: 成功したスキル構造パターンを他プロジェクトへ自動適用

### 参照論文

- arXiv:2605.15871 — AIRA の原論文（論文コード未公開、2026-05 時点）

### rl-anything との関係

現在の実装ロードマップ（FORGE / LBYL / ALSO）は AIRA の前段として機能する。
FORGE の `evolution_memory` が蓄積した成功パターンは AIRA の訓練データとなりうる。
