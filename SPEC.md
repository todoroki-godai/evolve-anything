# SPEC.md — rl-anything

Last updated: 2026-06-03 by /spec-keeper update (evolve 自己解析→issue 半自動起票 #299, ADR-033)

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
| **fleet 観測・介入** | fleet (`bin/rl-fleet`) | 全 PJ 横断で env_score / 導入状況を単一コマンドで可視化、Phase 分け実装（Phase 1: `status`）([ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md))。`recall` で全 PJ memory を keyword 決定論横断検索（LLM/embedding 非依存、[ADR-025](docs/decisions/025-cross-pj-memory-recall-keyword-only.md)）。`plugins` でインストール済み CC プラグインの最新性を決定論診断（`installed_plugins.json`↔`marketplace.json`↔cache の3点照合で ok/update/drift/unknown。version 無しプラグインの silent stale を検出し、git-sha 版は HEAD 比較で content-diff のスコープ不一致 FP を回避） |
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

Observe hooks (21個 registered, LLMコストゼロ) → テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善。UserPromptSubmit に HASP-style pitfall_injector 追加（エラー閾値検知で pitfalls.md を自動 inject）。Stop hook に `auto_memory_runner` 追加（corrections → L2 memory 候補を非同期生成、LLM 1 call 上限）。生成後は `belief_entropy` 決定論ゲート（生成要約の retention/drift を similarity 集合演算で近似採点、LLM ゼロ）が低信頼要約を書込前に破棄し `belief_blocks.jsonl` へ記録（#285）。pitfall 自動強制 hook 2個（`pitfall_lint` PostToolUse=警告のみ / `pitfall_commit_gate` PreToolUse Bash=danger を exit 2 ブロック）を追加 — `enable` 登録済み pitfalls.md にのみ反応するオプトイン方式（[ADR-027](docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md)）。audit は未登録だが育っている（エントリ3+件）pitfalls.md を `Unmanaged Pitfalls` セクションで可視化し enable へ誘導（`pitfall_registry.unmanaged_candidates` + `build_unmanaged_pitfalls_section`、liveness 判定は `parse.count_entries`、glossary 同様 evolve のたびに surface ＝ install≠enforcement の可視化）。observability 行（glossary_drift / unmanaged_pitfalls / belief_blocks / calibration_drift / eval_saturation / negative_transfer）は `audit/observability.py` の `_OBSERVABILITY_BUILDERS` を**単一ソース**とし、markdown 経路（`report.generate_report`）と構造化経路（`collect_observability` → evolve が `result["observability"]` に格納し SKILL.md Step 3.8 で必ず surface）の両方が同じリストを消費する（217KB markdown の選択読みで observability 行が埋もれて surface されない問題＝silence≠evaluated の再発を、生成側でなく出力経路の契約で塞ぐ、[ADR-028](docs/decisions/028-observability-contract-audit-evolve.md)）。`belief_blocks`（belief_entropy ゲートの block 件数、#285）と `calibration_drift`（fitness 評価関数の score-acceptance 相関 drift、#286）も同 contract 経由で evolve のたびに surface。calibration drift は `fitness_evolution.detect_drifted_funcs` を audit section と trigger_engine（session 終了時に evolve-fitness を proactive 提案、変更は人間承認 MUST）が共有する単一ソース。`negative_transfer` は `compute_component_transfer`（#288）で各追加スキルを更新コンポーネントとみなし、隣接追加イベントで before/after を区切る isolation window（after_i = before_{i+1}）で「どの更新が既存スキルを回帰させたか」を分離帰属する（arXiv 2605.30621 ablation、単一転移点版の誤帰属を回避）。`eval_saturation`（#292、`eval_saturation.py` + `build_eval_saturation_section`）は forward-gen trigger eval の飽和兆候（positive 偏重 / 易しい negative / クエリ過少）を eval 実行なし・決定論で測り、calibration drift と同帯で surface（緑＝頑健か飽和かを判別、TASTE arXiv 2605.28556 着想）。
スキル25個（ユーザー向け: evolve/audit/reflect/discover/prune/cleanup/handover/implement/spec-keeper/second-opinion/agent-brushup/breakthrough/backfill/import/pitfall-curate 等。内部/deprecated: reorganize/enrich/rl-loop-orchestrator 等）、共通ロジック14パッケージ（scripts/lib/ 配下、audit/discover/fleet/rl_common 等パッケージ化済み。`pipeline_eval.py` / `skill_importer.py` / `pitfall_manager/injector.py` / `meta_quality.py` / `similarity.py` / `trigger_eval_generator.py` / `skill_triage.py` / `world_context.py` / `memory_temporal.py` (importance_score・reinforce_memory・write_importance_score) / `skill_evolve/rubric.py` (rubric_checkpoint) / `fitness_history_store.py` (DuckDB fitness 履歴 SoR、NaN guard 付き冪等 ingest) / `hypothesis_tracker.py` (VeriTrace Phase 1、仮説ツリー JSONL 永続化) / `skill_extractor/` (SIRI ① 成功軌跡採掘。trajectory_sampler が raw セッションから TrajectoryRecord 抽出 → skill_extractor が generalizability_score 付き候補を生成。`run_discover` が project スコープで発火し triage の missed_skill_opportunities へ合流、discover=evolve recurring ループに配線済 #291 [ADR-030](docs/decisions/030-skill-extractor-discover-wiring-project-scoped.md)) / `glossary_drift.py` (CONTEXT.md 用語集 drift 検出、構造 gate + 未登録 jargon advisory) / `audit/observability.py` (observability セクションの単一ソース `_OBSERVABILITY_BUILDERS` + `collect_observability`、markdown/構造化の両経路が消費、[ADR-028](docs/decisions/028-observability-contract-audit-evolve.md)) / `belief_entropy.py` (生成後 memory 要約の retention/drift 決定論ゲート、similarity 再利用・LLM ゼロ、#285) 追加。jaccard 数式は `similarity.jaccard_coefficient` に一本化（memory_gating/meta_quality/episodic_store/belief_entropy の4重複を統合））、bin/ コマンド16個（`rl-backfill-turn-indices` / `rl-gain` / `rl-prompt-compare` 含む）、適応度関数8個組み込み（`default` / `skill_quality` / `coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `plugin`）、userConfig 17項目（`correction_preflight_threshold` / `error_preflight_threshold` / `skill_lr_budget` 追加）。evolve パイプラインに意図確認層（`intention_check` in `regression_gate.py`）と LR Budget gate（`skill_lr_budget` デフォルト30行）を組み込み、high-risk 変更を BLOCK。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/rl-anything:evolve`（日次）, `/rl-anything:audit`（診断）, `/rl-anything:reflect`（フィードバック反映）, `/rl-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全33件（最新: [ADR-033](docs/decisions/033-evolve-introspect-self-analysis-issue-filing.md) evolve 完了後に `result` 全体を決定論で読み 3 カテゴリ[提案矛盾 / phase 例外 / 系統的却下]の issue 候補を生成する自己解析を、observability builder（project_dir のみ）でなく独立モジュール `evolve_introspect` として実装。`run_evolve` 末尾配線で evolve のたびに自動発火、SKILL Step 11 が人間承認後 todoroki-godai/rl-anything へ半自動起票、body マーカーで root cause 単位 dedup、#299）。直前: [ADR-032](docs/decisions/032-claude-md-skills-parser-coverage-and-resolution-tristate.md) CLAUDE.md Skills パーサ記法拡張 + 解決状態3分類（#295）。SkillOS（Frozen Executor + Trainable Curator）/ MemOS（4層メモリ結晶化）対応設計の詳細は [spec/key-design-decisions.md](spec/key-design-decisions.md) を参照。カテゴリ別要約は [spec/architecture.md](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、ADR 原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近の変更概要（完全な履歴は [CHANGELOG.md](CHANGELOG.md)）:
- 2026-06-03: **feat(evolve): evolve 実行後の自己解析で issue 候補を半自動起票 (#299)** — evolve 自身の実行結果（提案の質・実行時エラー・改善余地）を振り返る経路が無く、パイプラインのバグは人間が手で issue を立てるまで構造に残らなかった（install≠enforcement と同型のメタ層の配線漏れ）。新規 `evolve_introspect.py` が `result` を決定論で読み 3 カテゴリの issue 候補を生成: ①self_detection（split↔archive 矛盾 / line budget 悪化提案）②runtime_errors（握り潰された phase 例外 / observability 取得失敗、`_error_signature` で root cause 正規化）③improvement_opportunities（系統的却下 type / calibration regression）。`run_evolve` 末尾が `result["self_analysis"]` に格納し evolve のたびに自動発火、0 件でも ✓ を残す（silence≠evaluated）。起票は半自動（SKILL Step 11 が per-item 提示→人間承認→`gh issue create --repo todoroki-godai/rl-anything` 固定）。重複起票防止は body マーカー `rl-evolve-introspect:<dedup_key>`（root cause 単位）→タイトル類似度の二段 dedup。observability builder は project_dir のみで result を読めないため独立モジュール化（[ADR-033]）。決定論・LLM 非依存（起票判断のみ人間）。TDD 新規19件 + 実 PJ E2E。[ADR-033]
- 2026-06-03: **fix(skill_triggers): CLAUDE.md Skills の太字ラベル+バッククォート形式を読めるようにし誤検知を解消 (#295)** — `_parse_skills_section` のリスト行パーサ `^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]` は `- /skill:` 系しか拾えず、`- **AWSデプロイ**: \`/aws-deploy\` - path` 形式を CLAUDE.md が在るのに trigger 0 件しか返せず、`claudemd_skills` 空集合 → 「CLAUDE.md 記載スキルは除外」が全滅し untagged_reference/missed_skills/skill_triage が誤検知していた（shadow でなく実体 project_dir 上で再現するパーサバグが真因）。`_extract_list_item_skill` で行内バッククォートコマンド `` `/cmd` `` を抽出（過剰捕捉は exclusion を広げる安全側）、`resolve_claude_md_path` で実体パス基準（直下→git ルート fallback）解決、`claude_md_unparseable` ゲートで「在るが trigger 0」時は untagged を suppress + 件数 surface（不在=正規 PJ は従来どおり検出）。実機 `sys-bots-main` で before 0→after 12 skills。TDD 新規12件 + API snapshot 更新。[ADR-032]
- 2026-06-03: **fix(optimize-history): accept/reject 履歴の split-brain を解消し DATA_DIR の PJ スコープに集約 (ADR-031)** — `history.jsonl`（fitness calibration 母集団）の読み書きが 3 経路に分裂（optimize/evolve-diff=plugin generations 更新リセット / run_loop=`cwd/.rl-loop` 孤立 / readers=plugin generations）し、更新で母集団がリセット・rl-loop 記録が calibration に未到達・永続 DATA_DIR に不在、が複合。実測で全 location union がユニーク 9 件・有効 3 件＝実質一度も累積せず（atlas-breeaders は他 PJ 由来の数字を自 PJ と誤認し「3/30」表示）。新規 `optimize_history_store.py` に集約し `DATA_DIR/optimize_history/<slug>.jsonl` の PJ スコープへ。slug は worktree 安全に `git --git-common-dir` 親 basename で解決（`--show-toplevel` basename は worktree 名で二次 split-brain 化するため不可）。読み書き 6 箇所を store 経由に差し替え、未使用 `RL_LOOP_DIR` を撤去、conftest autouse 隔離に追加。migration は非実装（有効 3 件 < BOOTSTRAP_MIN、ADR-031 Decision 5）。TDD（store 単体 + split-brain 回帰 E2E + 6 箇所回帰、影響 748 件緑）。[ADR-031]
- 2026-06-03: **feat(audit): trigger eval 飽和度を observability contract に surface (#292, TASTE)** — `trigger_eval_generator` は順生成のみで「緑なのに頑健か飽和か」を判別する経路が無かった。arXiv 2605.28556「TASTE」着想で `eval_saturation.py` が forward-gen eval set の飽和兆候を eval 実行なし・LLM 非依存・決定論で測る（`low_negative_coverage`/`easy_negatives`（trigger 語が取れる skill のみ）/`thin`）。`build_eval_saturation_section`（`audit/sections_eval.py`、sections.py が hard 行数 800 到達のため分離）を `_OBSERVABILITY_BUILDERS` の calibration_drift 直後に登録し markdown/構造化の両経路へ自動伝播 — evolve のたびに calibration drift と同帯で surface。未生成環境=対象外(None)/飽和なし=✓/飽和あり=⚠（silence≠evaluated）。eval-sets は環境グローバル（DATA_DIR 配下）で trigger 語は PJ 依存のため calibration_drift と同じグローバル系 builder。新規テスト15件、実機 eval-sets 8件で E2E 確認。[ADR-028]
- 2026-06-03: **feat(discover): SIRI ① skill_extractor を run_discover に配線 (#291)** — #238 Phase 1 で実装済みだが「どの recurring ループからも呼ばれない」休眠状態だった成功軌跡採掘モジュール（install≠enforcement と同型の配線漏れ）を `run_discover` に接続。`extract_skill_candidates` を project スコープ（`_project_transcript_dir` で CC エンコード `/`・`.`→`-` 変換、cross-PJ noise 防止）で発火し、`generalizability_score >= TRAJECTORY_SKILL_SCORE_THRESHOLD`（既定 0.25、noise lever）でフィルタして `trajectory_skill_candidates` に surface、純粋ヘルパー `_trajectory_candidates_to_missed` で triage 互換の missed_skills 形式へ変換し既存の `missed_skill_opportunities` 合流点へ接続（CREATE/UPDATE 判定 + meta_quality_check に合流）。discover は evolve Phase 2.6 が消費する recurring ループなので evolve のたびに自動発火。決定論・LLM 非依存。TDD（純粋ヘルパー + run_discover surface + エンコード契約 + 例外処理、7件）+ snapshot 更新 + 実 PJ E2E（trajectory_skill_candidates surface 確認）

## Current Limitations / Known Issues

詳細は [spec/limitations.md](spec/limitations.md) を参照。主な制限: episodic 層 audit 未統合、subagent token 二重カウント可能性、CLAUDE.md レイヤーは reflect 反映のみ。

## Next

近期の作業項目（warn 超ファイル分割、fleet Phase 2/3、perf、既知バグ、Subagents 進化等）は [spec/next.md](spec/next.md) を参照。

## 長期ロードマップ
AIRA（スキル構造自動探索エンジン、設計構想段階）の詳細は [spec/roadmap.md](spec/roadmap.md) を参照。
