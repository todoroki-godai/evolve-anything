# SPEC.md — evolve-anything

Last updated: 2026-08-11 by /spec-keeper update — 毎朝の無人パイプライン2件を反映（#408/#410 llm_judge Phase B の非対話化 + `safe_llm_call` 4重防御・費用の事前予約 / #409/#412 SessionStart の改善案 y/n 提示・global レーン）。hot は「毎朝の無人パイプライン」1段落 + コンポーネント表1行、詳細は cold（`spec/components-feedback.md` / `spec/components-daily.md`）。あわせて stale だった構成数値を実測へ是正（共通ロジック 24→25 パッケージ・bin コマンド 24→25）。前回: 2026-08-10 #379 Step 4「段階削除」完走を反映（PR #395-#399: detect-deferred-task / judge_audit / quality-scores / growth-journal の各 harness 削除 + 戦果ボード `results_board.py` 置換 + 未登録 live store 11件の宣言バックフィル・StoreKind "json" 新設・store_write の kind=json reject ガード。dead ストア 0件）。hot の成長可視化行は PR #398 内で更新済み、cold は `spec/components-observability.md`（store_registry / store_write）を本 update で追随。#379 close 済み・残ギャップは #400/#401/#402 へ引き継ぎ。前回: 2026-08-02 (recovery) #352/#353 反映 + L2 cold 分割

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
| フィードバック | reflect, report-feedback | reflect=修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映。approve 時に episodic 層（DuckDB TTL 30d）に昇格し、次セッションで「N日前に対処済み」として重複修正を検出。report-feedback=evolve/audit レポートを LLM メタレビューし evolve-anything 自身への改善 issue を半自動起票（決定論 `evolve_introspect` が拾えない「読んで気づく」改善が対象、旧 feedback の後継、詳細は [spec/components.md](spec/components.md)） |
| 直接パッチ最適化 | evolve-loop, generate-fitness, evolve-fitness | GA廃止、LLM 1パス直接パッチ ([ADR-003](docs/decisions/003-direct-patch-over-genetic-algorithm.md)) → regression gate。optimize は CLI/内部呼び出し専用（`bin/evolve-optimize`、evolve-loop から起動） |
| **fleet 観測・介入** | fleet (`bin/evolve-fleet`) | 全 PJ 横断で env_score / 導入状況を単一コマンドで可視化、Phase 分け実装（Phase 1: `status`）([ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md))。`recall` で全 PJ memory を keyword 決定論横断検索（LLM/embedding 非依存、[ADR-025](docs/decisions/025-cross-pj-memory-recall-keyword-only.md)）。`plugins` でインストール済み CC プラグインの最新性を決定論診断（`installed_plugins.json`↔`marketplace.json`↔cache の3点照合で ok/update/drift/unknown。version 無しプラグインの silent stale を検出し、git-sha 版は HEAD 比較で content-diff のスコープ不一致 FP を回避）。`queue` で学習素材ベースに「今 evolve すべき PJ」を決定論・ゼロ LLM で列挙（material_count = weak 未処理 + 前回 evolve 以降の新規 corr ≥ 閾値〔既定5・env `EVOLVE_QUEUE_THRESHOLD`〕。per-PJ `last_evolve_at` を新ストア `evolve-queue-state.jsonl`〔store_registry active・store_write barrier・`evolve --drain` apply 境界で書込〕で PJ 別に測る。daily-evolve Epic #78 Phase 1a・`--json` は Phase 1b #80 契約・#79・[ADR-050](docs/decisions/050-daily-evolve-pull-learning-material.md)）。dogfood 修正で実 dir 不在の dead/旧名 PJ を `skipped_dead` に分離して透明化、各 entry に `project_path` 付与（#79）、`activity_since.sessions` をハードコード 0 から `aggregate_sessions_by_project`（session_store union read・distinct session_id）で実値化（#85）、tracked 母集団に居ないが学習素材を持つ untracked PJ（実 dir gate 付き・phantom 除外）を `untracked_with_material` に **advisory surface**（auto-track せず・footer `M tracked (config)`・#86）。**手動運用入口スキル `/evolve-anything:queue`**（read-only ゼロ LLM）が待ち一覧→`/cd <PJ>`→`/evolve-anything:evolve` を案内（#80 launchd 自動登録の代替・pull 型 ADR-050）。`propose`（#81）が queue 待ち PJ への dry-run 提案バッチを無人生成、`pr-start`/`pr-finish`（#82）が承認済み提案を worktree→commit→push→PR 化（pr-start の分岐元は origin 既定ブランチ固定・pr-finish は commit より先に account 検証）— daily-evolve Epic #78 完結＝①無人列挙 ②propose 提案 ③人間ゲート適用+PR |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・upstream 監視 |
| セカンドオピニオン | second-opinion | Claude Agent による cold-read 独立見解（codex 代替、3モード） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| **成長可視化 (NFD)** | audit --growth | NFD 論文ベースの Spiral Development Model — フェーズ自動判定 + Lv.1-10 レベルシステム + 🏆 戦果ボード（手直し回数の増減・採用した改善 accepted/rejected/pending/excluded・取り下げ候補、#379 Step 4）。crystallized_rules 計測の廃止（growth-journal harness 削除）に伴い Mature Operation への昇格判定は保留中（旧「環境プロファイル・成長ストーリー」は戦果ボードへ置換済み） |
| **ROI 可視化** | evolve-gain (`bin/evolve-gain`) | `rtk gain` 風 ASCII レポート — 推定節約時間・Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示 |
| **コミュニティスキル import** | import (`bin/evolve-fleet import`) | コミュニティリポジトリからスキルをワンコマンドで取得・インストール。`owner/repo`・ローカルパス・URL に対応。scripts/ 自動実行なし、[y/N] confirm のセキュリティゲート付き |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（マージ済みブランチ / remote refs / 一時 worktree / 一時ディレクトリ / 関連 Issue close 候補 / PR Test plan 残件 / CC プロジェクト状態パージ Category 7）を候補提示→`AskUserQuestion` 個別承認→実行で安全処理。一時ディレクトリ default prefix は `evolve-anything-` のみに限定 ([ADR-021](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes` / userConfig で拡張可能 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存スキル。`seed`（正準ひな型生成）/ `normalize`（既存ファイルを正準形へ冪等変換）/ dedup（jaccard、日本語は CJK bigram、Root-cause 不在時は本文 fallback）/ 普遍性分類（`Transferability` universal/project/instance + `Generality` 1-5）/ 配布版(Top-N) / 同期ゲート。パーサは正準・`## N.`番号付き・インラインパイプ・`<!-- -->`スキップに対応（収束路線、[ADR-027](docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md)）。判断は agent、決定論処理は `scripts/core.py`（curate）+ `parse.py`（フォーマット I/O）。`similarity.py` 再利用。`pitfall_manager`（自己進化専用）とは別ライフサイクルで共存（[ADR-026](docs/decisions/026-pitfall-curate-vs-pitfall-manager.md)） |
| モデルティア管理 | evolve-tier (`bin/evolve-tier`) | HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort の正典を `~/.claude/model-tiers.json` に一元化する CLI（#193）。正典散在（model-routing rule / `agent_tier.TIER_POLICY` / 各 PJ agent frontmatter / settings.json）による手動追従漏れ（2026-07-10 opus 4.8 廃止時に HEAD が fable⇄sonnet を往来した実例）を解消。`set`（正典更新）と `sync [--apply]`（targets 明示列挙のみへ反映・既定 dry-run・冪等）を分離、`drift` で stale なモデルエイリアスの散文残存を advisory 検出（書換はしない）。`agent_tier` gate は call-time でこの config を参照。対話 UX ラッパー `/evolve-anything:tier` スキルを同梱（CLI 直叩き不要・sync --apply は明示承認後のみ） |

「4本目の柱」は fleet 観測・介入としての evolve-anything 拡張。per-PJ 自己進化から fleet 自己進化への昇格（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）。

Observe hooks（24個登録・LLMコストゼロ）→ テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善の基本ループに加え、報酬信号レイヤー（`utterance_archive`/`weak_signals`/`correction_semantic` 等）、pitfall 自動強制 hook、observability contract（`_OBSERVABILITY_BUILDERS` 単一ソース、markdown/構造化 両経路が消費）、環境衛生検出器群（artifact/memory 衛生・agent_team・hook_drift 等）を備える。詳細は [spec/architecture.md](spec/architecture.md#observe--報酬信号レイヤー詳細) を参照。
スキル25個・共通ロジック25パッケージ（`scripts/lib/` 配下）・bin/ コマンド25個・適応度関数8個組み込み（`default`/`skill_quality`/`coherence`/`telemetry`/`constitutional`/`chaos`/`environment`/`plugin`）・userConfig 21項目で構成。個別モジュール・パッケージの詳細は [spec/architecture.md](spec/architecture.md#observe--報酬信号レイヤー詳細) を参照。

**毎朝の無人パイプライン（#408 / #409）**: `bin/evolve-daily-run`（launchd・毎朝1回）が ingest → tokens → `fleet detect` → **llm_judge Phase B（意味判定）** → `fleet queue --json` + **改善案 digest** の順に走り、`evolve-queue.json` に「待ち PJ」と「改善案そのもの」を書く。SessionStart hook がそれを読み、**コマンドを叩かずに** 改善案を最大2件 y/n 提示する（案が無い日は完全沈黙）。無人でやるのは候補づくりまでで、採否は必ず人間の y/n（無人適用しない）。判定の日次上限は件数/トークンの userConfig 2本で、無人 `claude -p` は `scripts/lib/safe_llm_call.py` 一点に集約し4重防御を張る。詳細は [spec/components-feedback.md](spec/components-feedback.md)（judge_runner / safe_llm_call）と [spec/components-daily.md](spec/components-daily.md)（proposal_digest）を参照。

**通し評価ゲート（#496）**: `bin/evolve-dogfood-gate`（pytest 非依存 CLI、ロジックは `scripts/lib/dogfood/`）がリリース前に「テスト緑・evolve 無エラー・でも成果物がバグだらけ」を3層で防ぐ — Layer1: dry-run SHA256 不変（隔離コピー方式 + 文書化された三層除外）+ 実PJ ingest E2E / Layer2: report invariants（observability contract 突合）/ Layer3: SKILL.md コードブロック抽出実行（素の起動経路）。PJ slug 導出は `scripts/lib/pj_slug.py` が単一ソース（#492: `resolve_pj_slug`=git-common-dir 親・authoritative / `pj_slug_fast`=文字列処理・hooks hot path 用。read/write 同一関数の原則）。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/evolve-anything:evolve`（日次）, `/evolve-anything:audit`（診断）, `/evolve-anything:reflect`（フィードバック反映）, `/evolve-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全52件（最新: [ADR-052](docs/decisions/052-claude-primary-codex-opt-in-lanes.md) Claude Code primary / Codex opt-in executor lanes — Claude Code を primary executor、Codex を opt-in の cold reviewer/独立 executor とし、top-level lane（`<issue>-<slug>`、1 owner/1 writer、owned_paths 非重複）契約・ownership 取得と repo外 worktree 作成の排他区間・SoT=commit + git-common-dir 外部 handoff 証拠・merge/release/Issue close は人間権限、を規定、Accepted、#268/#266）。ADR-051 以前の履歴は [spec/key-design-decisions.md](spec/key-design-decisions.md#直近-adr-履歴) を参照。SkillOS（Frozen Executor + Trainable Curator）/ MemOS（4層メモリ結晶化）対応設計の詳細は [spec/key-design-decisions.md](spec/key-design-decisions.md) を参照。カテゴリ別要約は [spec/architecture.md](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、ADR 原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

変更履歴は **[CHANGELOG.md](CHANGELOG.md) が単一ソース**。ここには転記しない（#318）。

hot な SPEC.md に「直近の変更」を手書きで持つと、CHANGELOG との二重メンテが必ず drift する
（この repo は宣言と実装の二重管理による drift を #375 / #400 で繰り返している）。直近 N 件を
hot に出したくなった場合も手書きせず、CHANGELOG からの決定論生成（派生物）にすること。

## Current Limitations / Known Issues

詳細は [spec/limitations.md](spec/limitations.md) を参照。主な制限: episodic 層 audit 未統合、subagent token 二重カウント可能性、CLAUDE.md レイヤーは reflect 反映のみ。

## Next

近期の作業項目（warn 超ファイル分割、fleet Phase 2/3、perf、既知バグ、Subagents 進化等）は [spec/next.md](spec/next.md) を参照。

## 長期ロードマップ
AIRA（スキル構造自動探索エンジン、設計構想段階）の詳細は [spec/roadmap.md](spec/roadmap.md) を参照。
