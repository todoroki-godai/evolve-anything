# SPEC.md — evolve-anything

Last updated: 2026-08-02 by /spec-keeper update (recovery) — #352/#353（icebox 3レーン決定論分類 `icebox_reconcile`）を Recent Changes に反映。構造対応: `spec/components-fleet.md` が単一ファイル閾値（>100KB）超過のため daily-evolve パイプライン + icebox 棚卸しを `spec/components-daily.md` へ分割、SPEC.md 本体が hot 閾値（>35KB）超過のため Observe/報酬信号レイヤー詳細を `spec/architecture.md`、Key Design Decisions の ADR 履歴（ADR-051 以前）を `spec/key-design-decisions.md` へ cold 移動。Key Design Decisions の最新を ADR-052（Claude Code primary / Codex opt-in executor lanes）へ更新。前回: 2026-07-31 #309/#267/#279/#283/#284/#286/#287/#290/#275/#277/#268 反映

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
| **成長可視化 (NFD)** | audit --growth | NFD 論文ベースの Spiral Development Model — 4フェーズ自動判定 + Lv.1-10 レベルシステム + 環境プロファイル（5 traits）+ 成長ストーリー |
| **ROI 可視化** | evolve-gain (`bin/evolve-gain`) | `rtk gain` 風 ASCII レポート — 推定節約時間・Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示 |
| **コミュニティスキル import** | import (`bin/evolve-fleet import`) | コミュニティリポジトリからスキルをワンコマンドで取得・インストール。`owner/repo`・ローカルパス・URL に対応。scripts/ 自動実行なし、[y/N] confirm のセキュリティゲート付き |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（マージ済みブランチ / remote refs / 一時 worktree / 一時ディレクトリ / 関連 Issue close 候補 / PR Test plan 残件 / CC プロジェクト状態パージ Category 7）を候補提示→`AskUserQuestion` 個別承認→実行で安全処理。一時ディレクトリ default prefix は `evolve-anything-` のみに限定 ([ADR-021](docs/decisions/021-cleanup-tmp-dir-prefix-safety.md))、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes` / userConfig で拡張可能 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存スキル。`seed`（正準ひな型生成）/ `normalize`（既存ファイルを正準形へ冪等変換）/ dedup（jaccard、日本語は CJK bigram、Root-cause 不在時は本文 fallback）/ 普遍性分類（`Transferability` universal/project/instance + `Generality` 1-5）/ 配布版(Top-N) / 同期ゲート。パーサは正準・`## N.`番号付き・インラインパイプ・`<!-- -->`スキップに対応（収束路線、[ADR-027](docs/decisions/027-pitfall-format-convergence-vs-tolerant-parser.md)）。判断は agent、決定論処理は `scripts/core.py`（curate）+ `parse.py`（フォーマット I/O）。`similarity.py` 再利用。`pitfall_manager`（自己進化専用）とは別ライフサイクルで共存（[ADR-026](docs/decisions/026-pitfall-curate-vs-pitfall-manager.md)） |
| モデルティア管理 | evolve-tier (`bin/evolve-tier`) | HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort の正典を `~/.claude/model-tiers.json` に一元化する CLI（#193）。正典散在（model-routing rule / `agent_tier.TIER_POLICY` / 各 PJ agent frontmatter / settings.json）による手動追従漏れ（2026-07-10 opus 4.8 廃止時に HEAD が fable⇄sonnet を往来した実例）を解消。`set`（正典更新）と `sync [--apply]`（targets 明示列挙のみへ反映・既定 dry-run・冪等）を分離、`drift` で stale なモデルエイリアスの散文残存を advisory 検出（書換はしない）。`agent_tier` gate は call-time でこの config を参照。対話 UX ラッパー `/evolve-anything:tier` スキルを同梱（CLI 直叩き不要・sync --apply は明示承認後のみ） |

「4本目の柱」は fleet 観測・介入としての evolve-anything 拡張。per-PJ 自己進化から fleet 自己進化への昇格（[ADR-022](docs/decisions/022-fleet-observation-plus-intervention.md)）。

Observe hooks（24個登録・LLMコストゼロ）→ テレメトリ JSONL → evolve/discover/reflect/audit → remediation → 自動改善の基本ループに加え、報酬信号レイヤー（`utterance_archive`/`weak_signals`/`correction_semantic` 等）、pitfall 自動強制 hook、observability contract（`_OBSERVABILITY_BUILDERS` 単一ソース、markdown/構造化 両経路が消費）、環境衛生検出器群（artifact/memory 衛生・agent_team・hook_drift 等）を備える。詳細は [spec/architecture.md](spec/architecture.md#observe--報酬信号レイヤー詳細) を参照。
スキル25個・共通ロジック24パッケージ（`scripts/lib/` 配下）・bin/ コマンド24個・適応度関数8個組み込み（`default`/`skill_quality`/`coherence`/`telemetry`/`constitutional`/`chaos`/`environment`/`plugin`）・userConfig 21項目で構成。個別モジュール・パッケージの詳細は [spec/architecture.md](spec/architecture.md#observe--報酬信号レイヤー詳細) を参照。

**通し評価ゲート（#496）**: `bin/evolve-dogfood-gate`（pytest 非依存 CLI、ロジックは `scripts/lib/dogfood/`）がリリース前に「テスト緑・evolve 無エラー・でも成果物がバグだらけ」を3層で防ぐ — Layer1: dry-run SHA256 不変（隔離コピー方式 + 文書化された三層除外）+ 実PJ ingest E2E / Layer2: report invariants（observability contract 突合）/ Layer3: SKILL.md コードブロック抽出実行（素の起動経路）。PJ slug 導出は `scripts/lib/pj_slug.py` が単一ソース（#492: `resolve_pj_slug`=git-common-dir 親・authoritative / `pj_slug_fast`=文字列処理・hooks hot path 用。read/write 同一関数の原則）。

コンポーネント構成・データフローの詳細は [spec/architecture.md](spec/architecture.md) を参照。

## API / Interface Spec

スキルコマンド一覧・適応度関数の詳細は [spec/api.md](spec/api.md) を参照。

主要コマンド: `/evolve-anything:evolve`（日次）, `/evolve-anything:audit`（診断）, `/evolve-anything:reflect`（フィードバック反映）, `/evolve-anything:optimize <skill>`（直接パッチ）

## Key Design Decisions

全52件（最新: [ADR-052](docs/decisions/052-claude-primary-codex-opt-in-lanes.md) Claude Code primary / Codex opt-in executor lanes — Claude Code を primary executor、Codex を opt-in の cold reviewer/独立 executor とし、top-level lane（`<issue>-<slug>`、1 owner/1 writer、owned_paths 非重複）契約・ownership 取得と repo外 worktree 作成の排他区間・SoT=commit + git-common-dir 外部 handoff 証拠・merge/release/Issue close は人間権限、を規定、Accepted、#268/#266）。ADR-051 以前の履歴は [spec/key-design-decisions.md](spec/key-design-decisions.md#直近-adr-履歴) を参照。SkillOS（Frozen Executor + Trainable Curator）/ MemOS（4層メモリ結晶化）対応設計の詳細は [spec/key-design-decisions.md](spec/key-design-decisions.md) を参照。カテゴリ別要約は [spec/architecture.md](spec/architecture.md#key-design-decisions-カテゴリ別サマリ)、ADR 原文は [docs/decisions/](docs/decisions/) を参照。

## Recent Changes

直近の変更概要（完全な履歴は [CHANGELOG.md](CHANGELOG.md)）:
- 2026-08-02: **icebox（凍結 issue）棚卸しの3レーン決定論分類 `icebox_reconcile` を追加（#352, PR #353）** — 凍結54件全件の再開条件を audit 出力と一発監査した結果、真の障害は「reopen-when 条件が自由文で判定不能」でなく「観測器（audit observability section 等）が動いていない」ことと判明し、成立（レーン1）/観測器不在（レーン2）/失効候補（レーン3）の3レーンに分類。主経路は daily runner が毎朝 `icebox-verdicts.json` を生成し SessionStart が成立分のみ名指し通知（既読ストア `icebox_verdict_seen.jsonl`）、保険経路は audit advisory（gh 非呼び出し）。既存54件中34件へ reopen-when ブロックを適用済み・初回 live 判定は成立1（#205 subagent_traces.first_try_success_rate 0.28<0.5）/観測器不在53、tech-eval 起票テンプレに reopen-when 必須化。**PR #353 レビュー**で reopen-when パーサの型/値域未検証・untrusted 値の echo・fingerprint 安定性（B5）・gh --limit truncation（B8）・file_lock 排他（P1）等20件を是正。詳細は [spec/components-daily.md](spec/components-daily.md)（icebox_reconcile）を参照。
- 2026-07-31（unreleased）: **評価実行条件（harness）の記録契約 `evaluation_provenance` を追加（#309）+ evolve decision lane の並行競合/取り違え修正（#267/#279/#283/#284/#286/#287/#290）+ self_contamination の曝露母数つき層別（#275/#277）+ Codex opt-in executor lane（#268）** — 評価スコアは **model × effort × tool policy × plugin version** という実行条件込みの束を測っているのに条件がどこにも残っておらず、スコア低下が「スキル劣化」か「判定モデル差」かを事後に分離できなかった（`judge_audit/harness.py` は `--model` を受け取りながら verdict に書いていなかった）。**過去分は遡及不能**なので比較ロジック（#240 で凍結継続）を待たず記録だけを先に始める。単一ソース `scripts/lib/evaluation_provenance.py` が envelope と責務分離4段（producer が捕捉 → 共通ビルダーが正規化 → 永続化境界 `finalize_provenance` が補完 → store はそのまま append）を提供し、**ストア層に条件を発見させない**（評価条件を知る手段が無く誤った provenance を生むため）。契約は「不明値を推測しない（None）」「非該当（決定論に `judge` キーなし・渡すと `ValueError`）と観測不能（`judge` は残し中身 None）を区別」「model alias は verbatim」。配線先5箇所: judge_audit verdict / constitutional の **layer cache 単位**（[ADR-037] 2 相では Phase B を対話セッションが生成するため Python から判定モデルを観測できず、集約時点では別時点・別モデルの layer が混ざり復元不能。集約は単一条件へ潰さず `judge_models` + `plugin_versions` + `harness_variants` + `mixed_provenance` で表現し、混在判定は model 単独でなく harness tuple 全体で行う）/ optimize_history の 3 writer（run_loop・genetic-prompt-optimizer・evolve-fitness）。組立に失敗しても記録は止めないが provenance キーごと落とさず `evaluation_kind=unknown` を残す（無記録だと契約 drift が全緑のままデータ欠損になる）。新ストア・write barrier 変更・遡及埋めは無し。時系列 SoR `fitness_history`（DuckDB）への provenance 列追加は schema migration を伴うため #316 に分離（#240 の前提）。設計は codex 外部 cold-read 2 回（設計時・PR レビュー時）で修正。詳細は [spec/components-core.md](spec/components-core.md)。
- 2026-07-23（続報, unreleased）: **diff-scoped 兄弟コピー検出 sibling_copy_guard 追加（#210）+ release-notes-review 軽量診断/ハイライト抽出/表示消失防止（#260）+ icebox_notice 閾値の userConfig化（#194）** — `sibling_copy_guard` は commit/push 対象の削除行を正規化し同一コードが他ファイルに未変更で残っていないかを検出、`pre-push.local` に非ブロッキング警告として配線（merge push時は非merge commit数上限15でノイズを抑制、実コーパス dry-run で個別commitとmerge push間の桁違いスケール差を実測）。release-notes-review は bug fixのみの差分でフル走査をスキップする軽量診断（Step 1.5）、🌟新機能ハイライト抽出（Step 3.0.1）、レポート出力をターン終端に固定し表示消失を防止する変更（Step 4→7再編、interleaved thinking で text ブロックが欠落する実測に対応）を追加。icebox_notice はハードコード90日を userConfig `icebox_review_threshold_days`（既定30）に変更（userConfig 総数 20→21）。詳細は [spec/components-feedback.md](spec/components-feedback.md)（sibling_copy_guard）・[spec/components-daily.md](spec/components-daily.md)（icebox_notice）を参照。
- 2026-07-23（unreleased）: **バグ修正2件 + fleet 軽量観測1件（#252/#253/#245）** — evolve-introspect 系 stale reference 検出（`audit/memory.py` の Memory Health セクション）がスラッシュ区切り列挙「A.md/B.md/C.md」を1つのネストパスと誤読し実在ファイルを confidence 0.95 で不在誤検知していた問題を、`path_extractor.split_enumeration_segments`（列挙形状の純テキスト判定）+ 過半数存在ゲート（"episodic.db/sessions.db/token_usage.db" のような DB ストア名の説明的言及等・非ファイル列挙を除外）+ 実ディレクトリ優先（"config.d/rules.json" 等の誤展開防止）の3段防御で修正、実コーパス較正で byte-identical を確認（#252）。correction-semantic の llm_judge weak_signal は複数トピック発言（主要な指摘＋ついでの別要望）の evidence.text に無関係な副次要望が同居していたが、`trim_to_idiom_sentence` が話題転換語（あと/ついでに等）の直前でのみセグメント分割し Haiku 抽出 idiom の属するセグメントだけを残す（idiom 不在/曖昧/話題転換語無しは全文フォールバック、idiom は llm_judge チャネル限定の契約のため rephrase 等には非適用、#253）。`evolve-fleet status`/`tokens` に codex CLI（`~/.codex/state_5.sqlite`）利用状況の軽量 advisory を追加 — PJ 別セッション数/`tokens_used`合計/最終利用時刻、DB不在/スキーマ相違は無音・ロック中等は警告1行のみで fail-open、CC 側 `token_usage_store` とは単位・粒度が異なるため合算しない（#245）。3件とも決定論・LLM 非依存・TDD 済み。詳細は [spec/components-fleet.md](spec/components-fleet.md)（#252/#245）・[spec/components-feedback.md](spec/components-feedback.md)（#253）を参照。
- 2026-07-19（unreleased）: **issue #234 — evolve-loop-orchestrator に winner's curse 補正 + ablation opt-in CLI を接続** — arXiv 2607.12227（test-time scaling 比較 + held-out 検証の要求）への対応。同一 LLM judge 単独評価環境では文字通りの「held-out スプリット」は交換可能なノイズサンプルに過ぎず統計的に無意味と判明した（tacchi レビュー）ため、看板でなく実質を優先する3PRで対応: (1) バリエーション生成の配線drift修理（`generate_variants()` が廃止済みオプションで常時失敗していた前提バグ、`variant_generation.py` 新設）、(2) 採用前再評価による winner's curse 補正（IMPROVED 候補のみ追加 N=3 回再評価し平均で verdict 再判定、既定 ON・`--no-selection-reeval` で無効化可、`selection_reeval.py` 新設）、(3) 設計文脈 vs naive 生成比較の opt-in 較正 CLI（`bin/evolve-loop-ablation`、単純サンプリング比較の実質）。issue は改訂した受け入れ基準コメント付きで close 済み。他に未リリースのバグ修正13件・機能拡張2件（#194-#229、subagent_traces 実測 effort 分布の tier drift 検証活用 #219 含む）が Unreleased に積み上がっている。詳細は [CHANGELOG.md](CHANGELOG.md) Unreleased 節を参照。
- それ以前（v1.122.0〔auto-memory project スコープ4層防御 #206 / subagent_traces 委任プロンプト保存 #200 / evolve keyset snapshot 二層 golden 化 #209、2026-07-17〕/ 2026-07-11 icebox 棚卸しの気づきトリガー #194〔daily runner が icebox closed issue を集計 → SessionStart で最古 N 日超を1行通知〕/ v1.121.0〔daily-evolve Epic #78 完結: #81 propose + #82 pr-start/pr-finish・#191 skill_reachability・#161 worker_takeoff、2026-07-10〕/ v1.120.0〔モデルティア一元管理 evolve-tier + tier スキル #193 / judge false-pass 欠陥注入監査 #188 / 記憶更新の遷移検証 TRUSTMEM 型 #93 / ask-before-fallback 明文化検査 #192 / TIER_POLICY opus 4.8 追従、2026-07-10〕/ 2026-07-06 #146 result 依存キャプチャ3項目の --drain 値運搬移植〔ADR-051〕/ v1.117.0〔writer クラッシュ修理 #156 / daily 増分 token ingest #157 / hook 残骸検出器 #155 / memory 汚染検出 #108 / skill_vuln フロー検出 #123、2026-07-06〕/ 環境衛生検出器8件 #124-#131〔2026-07-03、v1.118.0 収録〕/ v1.111.0〜v1.115.0〔verbosity 学習ループ #75 / subagent_traces #38 / reward_ema #64 / predictive_validity #42 / advisory 共通枠 #115 ほか、2026-07-02〕/ queue 手動運用入口 + daily launchd 自動実行 + dogfood 修正3件〔#78/#79/#80/#85/#86、2026-06-25〕/ v1.110.0〔#50 レポート平易化 / #53 fleet status --json / #62 skill_quality red flags〕/ v1.109.0 audit report UX #48/#49/#51 / ② 物理単一化不採用の ADR-049 固定〔docs-only #55、2026-06-21〕/ v1.108.0 read層 union+alias #46 / ② write barrier #55〔ADR-049〕+ 観測 section #14 fanout_cost / #19 memory_capability / テレメトリ・メモリ基盤 #2/#36/#37/#40 + 移植 open issue 一掃 33→13 / v1.104.0 report-feedback スキル新設 #582 + sys-bots フィードバック6点 #583-#588 / #531 evolve.py パッケージ分割 ADR-048 / v1.100.0 繋ぎ目バグ Wave1-7 #521-#529/#185 / v1.99.0 通し評価ゲート #484-#496 / v1.98.0 報酬閉ループ #461-#469 以前）の詳細は [CHANGELOG.md](CHANGELOG.md) を参照

## Current Limitations / Known Issues

詳細は [spec/limitations.md](spec/limitations.md) を参照。主な制限: episodic 層 audit 未統合、subagent token 二重カウント可能性、CLAUDE.md レイヤーは reflect 反映のみ。

## Next

近期の作業項目（warn 超ファイル分割、fleet Phase 2/3、perf、既知バグ、Subagents 進化等）は [spec/next.md](spec/next.md) を参照。

## 長期ロードマップ
AIRA（スキル構造自動探索エンジン、設計構想段階）の詳細は [spec/roadmap.md](spec/roadmap.md) を参照。
