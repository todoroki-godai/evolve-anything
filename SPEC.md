# SPEC.md — rl-anything

Last updated: 2026-05-19 by /spec-keeper update (recovery)

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
| フィードバック | reflect | 修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映 |
| 直接パッチ最適化 | rl-loop, generate-fitness, evolve-fitness | GA廃止、LLM 1パス直接パッチ ([ADR-003](docs/decisions/003-direct-patch-over-genetic-algorithm.md)) → regression gate。optimize は CLI/内部呼び出し専用（`bin/rl-optimize`、rl-loop から起動） |
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を単一コマンドで可視化、Phase 分け実装（Phase 1: `status`）([ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)) |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・upstream 監視 |
| セカンドオピニオン | second-opinion | Claude Agent による cold-read 独立見解（codex 代替、3モード） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| セッション管理 | handover | 作業状態を構造化ノートに書き出し（ローカルファイル or GitHub Issue）、SPEC.md 同期、別セッションへ引き継ぎ |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| **成長可視化 (NFD)** | audit --growth | NFD 論文ベースの Spiral Development Model — 4フェーズ自動判定 + Lv.1-10 レベルシステム + 環境プロファイル（5 traits）+ 成長ストーリー |
| **ROI 可視化** | rl-gain (`bin/rl-gain`) | `rtk gain` 風 ASCII レポート — 推定節約時間・Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（マージ済みブランチ / remote refs / 一時 worktree / 一時ディレクトリ / 関連 Issue close 候補 / PR Test plan 残件 / CC プロジェクト状態パージ Category 7）を候補提示→`AskUserQuestion` 個別承認→実行で安全処理。一時ディレクトリ default prefix は `rl-anything-` のみに限定 ([ADR-021](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes` / userConfig で拡張可能 |

「4本目の柱」は fleet 観測・介入としての rl-anything 拡張。per-PJ 自己進化から fleet 自己進化への昇格（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）。

Observe hooks (15個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。
ユーザー向けスキル19個 + 内部スキル（reorganize / enrich deprecated）、共通ロジック14パッケージ・116モジュール（scripts/lib/ 配下、audit/discover/fleet/rl_common 等パッケージ化済み）、bin/ コマンド19個（`rl-gain` / `rl-score-noise` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）。

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

- 2026-05-19: **Phase 8-13 scripts/lib パッケージ化完了（PR #117-#146）** — `audit/` に続き `pitfall_manager/` / `skill_evolve/` / `trigger_engine/` / `pipeline_reflector/` / `coherence/` / `telemetry_query/` / `rl_common/` の 7 パッケージ化を完了（各 Phase 4〜5 Slice、snapshot test で API 不変保証）。`scripts/lib/` は 14 パッケージ・116 モジュール構成に移行（旧: 48 フラットモジュール）
- 2026-05-19: **feat(telemetry): r^comp / r^fc を telemetry fitness 5 軸に追加 (closes #67)** — `rate_completion` (r^comp) / `function_call_validity` (r^fc) を `scripts/lib/telemetry_query/` と `scripts/rl/fitness/telemetry.py` に統合。`audit` レポートに LSP 導入提案セクション追加（`feat(audit)` #161）
- 2026-05-19: **feat(memory): update_count guard による LLM 自己更新メモリ劣化検出 (closes #97)** — `post_tool_use_memory.py` hook が Write/Edit 後に `update_count` を自動インクリメント（#151/#153）、`update_count_guard.py` が閾値超過メモリを警告 (#147)
- 2026-05-14: **`scripts/lib/audit.py` 2046行モノリスを `audit/` パッケージに分割完了 (Phase 2、PR #51-#61)** — 11 サブモジュール に分離、`__init__.py` は 178 行の re-export 層のみに到達 (**-91%**)。Python source 行数バジェット guard (`MAX_PYTHON_SOURCE_LINES=500` warn / `MAX_PYTHON_SOURCE_HARD=800` violation) を `audit.check_python_source_budgets` として統合
- 2026-05-11: **v1.46.0 リリース — Token Consumption Tracking + ingest redesign** — `token_usage.db` (DuckDB SoR) に PJ 別トークン消費を冪等取り込み。`rl-fleet tokens` サブコマンド、`audit` レポートに "Token Consumption" セクション統合。closes #24, #28
### 4層メモリ結晶化（MemOS 対応設計）

rl-anything の corrections→evolve パイプラインは MemOS / HiMem（arXiv:2601.06377）の
L1→L4 結晶化アーキテクチャと同型の設計を採用する（[ADR-024](docs/decisions/024-memory-crystallization-memos-correspondence.md)）。

| MemOS 層 | rl-anything 対応 |
|---------|-----------------|
| L1 トレース | `corrections.jsonl` / `sessions.jsonl` 等（Observe hooks が記録） |
| L2 ポリシー | `MEMORY.md` (auto-memory、`/reflect` で更新) |
| L3 ワールドモデル | `rules/*.md` + `CLAUDE.md`（`/evolve` で昇格） |
| L4 結晶化スキル | `.claude/skills/*.md`（`skill_triage` / `/evolve-skill` で生成） |

**ギャップマッピング（将来検討）**:

- **未実装: 層間矛盾検出・自動 reconsolidation** — L2（MEMORY.md）と L3（rules）の
  矛盾エントリを検出する仕組みがない。MemOS が定義する下向き伝播（上位層→下位層更新）も未実装
- **未実装: ハイブリッド検索** — MEMORY.md は現状線形スキャン。MemOS/HiMem が提案する
  ベクトル検索 + 構造検索のハイブリッドは未実装
- **参照**: MemOS/HiMem (Zhang et al., 2026, arXiv:2601.06377)、[ADR-024](docs/decisions/024-memory-crystallization-memos-correspondence.md)

## Current Limitations / Known Issues

- **Token usage v1: subagent token は分離追跡しない** — CC 仕様により subagent 呼び出しの token 消費は親メッセージの `message.usage` に内包される（281k メッセージ実測で `isSidechain=true` 0 件確認済）。CC 側で `isSidechain` がマークされる版が出れば v2 で対応
- Subagents レイヤー: 乱立検知（SubagentStop hook + systemMessage 警告）は実装済み。観測・測定・進化の高度化は未着手（roadmap 参照）
- CLAUDE.md レイヤーの進化は reflect 反映のみ（自動修正なし）
- openspec/specs/ は機能仕様のみ（ADR 変換対象なし）。ディレクトリはアーカイブとして残存
- audit の CLAUDE.md line_limit_violation は warning_only

## Next

- **warn 超 5件の対応** — `agent_quality.py` (531行) / `reflect_utils.py` (534行) / `workflow_checkpoint.py` (462行) / `skill_triage.py` (458行) / `layer_diagnose.py` (433行) / `audit/orchestrator.py` (420行) が warn 閾値 (500行) 超。hard (800行) 到達時に fleet パターンで分割（`reflect_utils.py` は scripts/lib/ 移動済み）
- fleet Phase 2: `bin/rl-fleet audit-all [--parallel N]` + global rules (`~/.claude/rules/*.md`) × PJ CLAUDE.md の名前衝突検出（意味的矛盾は Phase 4+）
- fleet Phase 3: `reflect-all` / `evolve-all` を dry-run default + `--apply` で実装、`rollback <ts>` + PJ 単位 opt-in マーカー必須（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）
- fleet perf 最適化: Phase 1 実測 12.9s / 7 PJ（設計目標 3s）。`growth-state-<slug>.json` 直読みキャッシュ経路を Phase 2 で検討
- `audit.py` duckdb `usage.jsonl` クエリの `Conversion Error: Malformed JSON` 根本修正（fleet が AUDIT_ERROR として surface する既存バグ）
- Subagents レイヤーの進化メカニズム（roadmap Phase 3）
- 6レイヤー全体の自律進化ループ完成（roadmap To-be）
