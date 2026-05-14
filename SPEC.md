# SPEC.md — rl-anything

Last updated: 2026-05-09 by /spec-keeper update (v1.45.0: prune dep guard #25)

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

Observe hooks (14個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。
ユーザー向けスキル19個 + 内部スキル（reorganize / enrich deprecated）、共通ロジック48モジュール（fleet / session_store / score_noise / skill_usage_stats 等含む）、bin/ コマンド18個（`rl-gain` / `rl-score-noise` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全22件。カテゴリ別要約は [spec/architecture.md#key-design-decisions-カテゴリ別サマリ](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近の変更概要。完全な履歴は [CHANGELOG.md](CHANGELOG.md) を参照。

- 2026-05-14: **`scripts/lib/audit.py` 2046行モノリスを `audit/` パッケージに分割完了 (Phase 2、PR #51-#61)** — 11 サブモジュール (memory / gstack / quality / issues / classification / artifacts / usage / scope / sections / report / orchestrator) に分離、`__init__.py` は 178 行の re-export 層のみに到達 (**-91%**)。snapshot test (`test_audit_api_surface_snapshot`) で公開 API 不変保証、各 PR でテスト 2083+ green を維持しながら squash merge。後続再発予防として **Slice 13 (PR #62)** で Python source 行数バジェット guard (`MAX_PYTHON_SOURCE_LINES=500` warn / `MAX_PYTHON_SOURCE_HARD=800` violation) を `audit.check_python_source_budgets` として `run_audit` に統合、`.claude/rules/file-size-budget.md` で運用ルール化。design doc: `~/.gstack/projects/evolve-anything/todoroki-main-design-20260514-130921.md`
- 2026-05-11: **v1.46.0 リリース — Token Consumption Tracking + ingest redesign** — `~/.claude/projects/<pj>/*.jsonl` の `message.usage` を DuckDB SoR (`token_usage.db`、PK uuid) に冪等取り込みし、PJ 別 LLM トークン消費を可視化。`rl-fleet status` に `TOKENS_30d`/`CACHE_HIT` 列、`rl-fleet tokens` サブコマンドで TOP-N / WoW スパイク / cache hit 異常 / PJ ドリルダウン (session/model/week) / `--backfill`、`audit` レポートに "Token Consumption" セクションを統合。実機検証で発覚した write amplification (DuckDB per-file checkpoint = O(N) flush) を `connection()` context manager + `session_progress` 差分 ingest で解消、rl-anything PJ 1 個 / `--days 7` = 41s 完走 (budget 60s)。closes #24, #28
- 2026-05-09: **v1.45.0 — prune に skill 削除時の import 依存検査を追加** (#25, PR #29) — `scripts/lib/prune.py` に `SkillDependencyError` + `check_import_dependencies(skill_path, repo_root)` を新設。`archive_file()` が skill ディレクトリ（`skills/<name>` 全体）を archive する際、他スキル/CLI からの `import` や `skills/<name>/` パス参照を `git grep -P --untracked` ベース（フォールバック: pure-Python）で検出し、参照ありで `force=False`（デフォルト）なら例外で archive を中断する。実機検証で発覚した既存バグ（`git grep -E` が PCRE 構文を解釈せず import 検出が常に 0 件返していた問題）も同時修正。`v1.44.0/1.43.0/1.42.0` で `optimize.py` が知らずに削除された再発防止
- 2026-05-09: **v1.44.1 — rl-loop 依存復旧 + README バイリンガル化** — `a9fa34a` で削除された `skills/genetic-prompt-optimizer/scripts/optimize.py` を復元（rl-loop が `DirectPatchOptimizer` / `OPTIMIZER_SCRIPT` に依存しており機能不全だったが v1.42.0/1.43.0/1.44.0 と気付かれず3バージョン経過していた）。SKILL.md は復元せず内部専用方針を維持。`optimize.py` の result dict キー誤り（`target_path` → `target`）も同時修正（CodeRabbit 検出、smoke test で動作確認済み）。README を `README.ja.md`（日本語SoT）+ `README.md`（英訳）の2層構成に再編し、実装乖離（スキル数 23→19、Hooks 数 12→14、削除済みスキル/hook 5件、stop_failure イベント名）を網羅修正
- 2026-05-08: **v1.43.0 リリース — audit 行数違反の構造的修正** — `MAX_RULE_LINES` を 3→10 に統一（CLAUDE.md `code-quality.md` と整合）、`audit.py` の行数チェックで plugin/global スキル（gstack 等のダウンロード品）を除外（実環境で違反 790件 → 0件）、`hooks/detect-deferred-task.py` を repo に取り込み（`CLAUDE_PLUGIN_DATA` env var 対応）+ テスト隔離で本番 jsonl 汚染を解消。`scripts/lib/audit.py` の LIMITS を `line_limit.py` 定数の参照に切り替え（SoT 統一）
- 2026-05-08: **subagent 乱立検知・抑制機能追加** — SubagentStop hook がセッション内 subagent 数をカウントし、閾値（デフォルト 5）到達時に `systemMessage` で警告を出力。`userConfig subagent_warning_threshold` で閾値調整可能（`plugin.json` / `rl_common.py` に追加）。`~/.claude/rules/subagent-guard.md` に Claude への抑制指示を追加。closes #20
- 2026-05-06: **CC v2.1.121/126 対応** — `skill_activation_log.py` 新設（Skill PostToolUse で `invocation_trigger` を `skill_activations.jsonl` に記録）。cleanup スキルに Category 7（claude project purge、オプション）追加。`workflow_context.py` にネスト検出を追加
## Current Limitations / Known Issues

- **Token usage v1: subagent token は分離追跡しない** — CC 仕様により subagent 呼び出しの token 消費は親メッセージの `message.usage` に内包される（281k メッセージ実測で `isSidechain=true` 0 件確認済）。CC 側で `isSidechain` がマークされる版が出れば v2 で対応
- Subagents レイヤー: 乱立検知（SubagentStop hook + systemMessage 警告）は実装済み。観測・測定・進化の高度化は未着手（roadmap 参照）
- CLAUDE.md レイヤーの進化は reflect 反映のみ（自動修正なし）
- openspec/specs/ は機能仕様のみ（ADR 変換対象なし）。ディレクトリはアーカイブとして残存
- audit の CLAUDE.md line_limit_violation は warning_only

## Next

- **既存の Python source 行数バジェット violation 15件の対応 (Slice 13 dogfooding)** — Slice 13 で導入した guard が現リポジトリで 15 件検出。hard 2 件 (`scripts/lib/fleet.py` 1070行 / `scripts/lib/discover.py` 1131行) と warn 13 件。次セッションで `/office-hours` から分割計画 (audit.py と同パターン) を起点に着手予定
- fleet Phase 2: `bin/rl-fleet audit-all [--parallel N]` + global rules (`~/.claude/rules/*.md`) × PJ CLAUDE.md の名前衝突検出（意味的矛盾は Phase 4+）
- fleet Phase 3: `reflect-all` / `evolve-all` を dry-run default + `--apply` で実装、`rollback <ts>` + PJ 単位 opt-in マーカー必須（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）
- fleet perf 最適化: Phase 1 実測 12.9s / 7 PJ（設計目標 3s）。`growth-state-<slug>.json` 直読みキャッシュ経路を Phase 2 で検討
- `audit.py` duckdb `usage.jsonl` クエリの `Conversion Error: Malformed JSON` 根本修正（fleet が AUDIT_ERROR として surface する既存バグ）
- Subagents レイヤーの進化メカニズム（roadmap Phase 3）
- 6レイヤー全体の自律進化ループ完成（roadmap To-be）
