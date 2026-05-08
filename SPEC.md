# SPEC.md — rl-anything

Last updated: 2026-05-08 by /spec-keeper update (v1.43.0 release)

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
| 直接パッチ最適化 | optimize, rl-loop, generate-fitness, evolve-fitness | GA廃止、LLM 1パス直接パッチ ([ADR-003](docs/decisions/003-direct-patch-over-genetic-algorithm.md)) → regression gate |
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を単一コマンドで可視化、Phase 分け実装（Phase 1: `status`）([ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)) |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・upstream 監視 |
| セカンドオピニオン | second-opinion | Claude Agent による cold-read 独立見解（codex 代替、3モード） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| セッション管理 | handover | 作業状態を構造化ノートに書き出し（ローカルファイル or GitHub Issue）、SPEC.md 同期、別セッションへ引き継ぎ |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| 哲学原則レビュー | philosophy-review | Claude Code native セッション履歴を Judge LLM で評価し、`category: "philosophy"` 原則の違反例を corrections.jsonl に注入 ([ADR-020](docs/decisions/020-philosophy-seed-distribution.md)) |
| **成長可視化 (NFD)** | audit --growth | NFD 論文ベースの Spiral Development Model — 4フェーズ自動判定 + Lv.1-10 レベルシステム + 環境プロファイル（5 traits）+ 成長ストーリー |
| **ROI 可視化** | rl-gain (`bin/rl-gain`) | `rtk gain` 風 ASCII レポート — 推定節約時間・Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（マージ済みブランチ / remote refs / 一時 worktree / 一時ディレクトリ / 関連 Issue close 候補 / PR Test plan 残件 / CC プロジェクト状態パージ Category 7）を候補提示→`AskUserQuestion` 個別承認→実行で安全処理。一時ディレクトリ default prefix は `rl-anything-` のみに限定 ([ADR-021](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes` / userConfig で拡張可能 |

「4本目の柱」は fleet 観測・介入としての rl-anything 拡張。per-PJ 自己進化から fleet 自己進化への昇格（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）。

Observe hooks (17個, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。
スキル21個、共通ロジック48モジュール（fleet / session_store / score_noise / skill_usage_stats 等含む）、bin/ コマンド18個（`rl-gain` / `rl-score-noise` / `rl-prompt-compare` 追加）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全22件。カテゴリ別要約は [spec/architecture.md#key-design-decisions-カテゴリ別サマリ](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近5件のみ。過去の変更は [CHANGELOG.md](CHANGELOG.md) を参照。

- 2026-05-08: **v1.43.0 リリース — audit 行数違反の構造的修正** — `MAX_RULE_LINES` を 3→10 に統一（CLAUDE.md `code-quality.md` と整合）、`audit.py` の行数チェックで plugin/global スキル（gstack 等のダウンロード品）を除外（実環境で違反 790件 → 0件）、`hooks/detect-deferred-task.py` を repo に取り込み（`CLAUDE_PLUGIN_DATA` env var 対応）+ テスト隔離で本番 jsonl 汚染を解消。`scripts/lib/audit.py` の LIMITS を `line_limit.py` 定数の参照に切り替え（SoT 統一）
- 2026-05-08: **subagent 乱立検知・抑制機能追加** — SubagentStop hook がセッション内 subagent 数をカウントし、閾値（デフォルト 5）到達時に `systemMessage` で警告を出力。`userConfig subagent_warning_threshold` で閾値調整可能（`plugin.json` / `rl_common.py` に追加）。`~/.claude/rules/subagent-guard.md` に Claude への抑制指示を追加。closes #20
- 2026-05-06: **CC v2.1.121/126 対応** — `skill_activation_log.py` 新設（Skill PostToolUse で `invocation_trigger` を `skill_activations.jsonl` に記録）。cleanup スキルに Category 7（claude project purge、オプション）追加。`workflow_context.py` にネスト検出を追加
- 2026-05-03: **rl-gain: ROI 可視化コマンド追加** — `bin/rl-gain` 新設。`usage.jsonl` のスキル呼び出しから推定節約時間を集計し、Growth Level・Efficiency meter・スキル別 Impact を `rtk gain` 風 ASCII レポートで表示。テスト 25件
- 2026-05-01: **rl-loop 採点ノイズ対策強化** — `rl-score-noise` (bin/ + `scripts/lib/score_noise.py`) で軸別スコア標準偏差を計測し epsilon 推奨値を出力。`_score_single_axis` リトライ機構追加（σ を 0.012〜0.029 に低減）。H_best 駆動ループ + IMPROVED/STABLE/REGRESSED(ε=0.05) verdict + Pareto dominance チェック実装

## Current Limitations / Known Issues

- Subagents レイヤー: 乱立検知（SubagentStop hook + systemMessage 警告）は実装済み。観測・測定・進化の高度化は未着手（roadmap 参照）
- CLAUDE.md レイヤーの進化は reflect 反映のみ（自動修正なし）
- openspec/specs/ は機能仕様のみ（ADR 変換対象なし）。ディレクトリはアーカイブとして残存
- audit の CLAUDE.md line_limit_violation は warning_only

## Next

- fleet Phase 2: `bin/rl-fleet audit-all [--parallel N]` + global rules (`~/.claude/rules/*.md`) × PJ CLAUDE.md の名前衝突検出（意味的矛盾は Phase 4+）
- fleet Phase 3: `reflect-all` / `evolve-all` を dry-run default + `--apply` で実装、`rollback <ts>` + PJ 単位 opt-in マーカー必須（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）
- fleet perf 最適化: Phase 1 実測 12.9s / 7 PJ（設計目標 3s）。`growth-state-<slug>.json` 直読みキャッシュ経路を Phase 2 で検討
- `audit.py` duckdb `usage.jsonl` クエリの `Conversion Error: Malformed JSON` 根本修正（fleet が AUDIT_ERROR として surface する既存バグ）
- Subagents レイヤーの進化メカニズム（roadmap Phase 3）
- 6レイヤー全体の自律進化ループ完成（roadmap To-be）
