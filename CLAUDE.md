# evolve-anything Plugin

> **Agent contract:** 作業開始前に
> [`docs/agent-contract/policy.md`](docs/agent-contract/policy.md) を全文読むこと。
> 通常のprimary executorはClaude Code、Codexはcold review・独立検証・ユーザー指定時に使う。
> runtime差は
> [`docs/agent-contract/capability-matrix.md`](docs/agent-contract/capability-matrix.md) が正典。

スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化**、**fleet 観測・介入** を提供する Claude Code Plugin。

## 目指すユーザー体験（全機能の判断基準）

> **「記録は全自動・判断は朝の30秒・効果は週1の数字で実感」**（2026-08-04 ユーザー合意・#379）

**これは到達目標であって現状説明ではない**。この節を新機能の採否判定に使うときは、目標文でなく
**実測値の側**を基準にする（#376「数字が嘘をつかない」を自分の看板文言にも適用する）。

**到達状況の数値をこのファイルに書かない**。日付付きスナップショットを正典に置くと必ず腐り、
「古い数字が正典に居座る」という #376 そのものの病気になる。現在値は下記が実行時に出すライブ値だけを
根拠にする（2026-08-15 codex レビュー）。

```bash
bin/evolve-audit          # 戦果ボード = 柱3（指摘率の測定可否・採用件数・除外内訳）
bin/evolve-revert --list  # 柱4（採用のうち戻せる件数）
```

1. **普段**: ユーザーは各PJで普通にチャットするだけ。プラグインの存在を忘れている（observe は全自動・無音）
2. **朝**: セッション開始の1行通知 → 改善案を**ユーザーの言葉**で1件ずつ提示 → y/n だけ。cd もコマンド暗記も不要
3. **週1**: 戦果ボードで事実を棚卸しする（採用件数・一発成功率・戻せる採用の一覧）。
   **効果の因果判定（「手直し回数の減少」＝(a) / 「採用した改善が効いたか」＝(b)）は分母が揃った
   項目だけ表示し、揃うまで `not_measured` と明示する**。効いていないものの自動取り下げも (b) が
   計測可能になって初めて発火する。**表示条件**: (a) は全量判定した週が4週連続（`correction_rate`）、
   (b) の分母は revert 済みを畳んだ有効 accept 件数（`results_board`）。**どちらも現在値は
   `bin/evolve-audit` で確認する**（ADR-054 §5・§7.2）
4. **信頼**: 表示する数字が嘘をつかない（#376）/ 適用は必ず人間の y/n（無人適用しない）/
   skill 採用は1コマンドで戻せる（**適用範囲: evolve drain 経由の新規採用のみ**。optimize.py 経路と
   evolve-loop 経路は revert 対象外＝ADR-054 Phase D PR2/PR3 を凍結した。採用実績が乏しく
   投資に見合わないため。使われ始めたら解凍する）

**判断規則**: 新機能・変更・tech-eval 取り込みは、この体験の 1〜4 のどこかを直接強化するものだけ採用する。この流れに登場しないものは icebox（#379 縮小方針）。

**新設凍結（#379 Step 1）**: 縮小完了まで新 store / observability section / advisory proposal adapter / weak_signal channel の追加は停止する（削除は許容）。単一ソースは `scripts/lib/shrink_freeze.py`。契約テスト（`test_shrink_freeze.py`）が CI portable suite で blocking 強制、pre-push light は同内容を非ブロッキング advisory として早期警告。store / weak_signal channel の runtime 書込みも `store_write_raw` / `append_signals` の凍結ゲートで reject する。`scaffold_advisory --write` も凍結中は拒否する。

**表示淘汰（#379 Step 2）**: 人間の行動に繋がった実証のない observability section 33 件を audit の表示から外す（**コードは削除しない・builder は `_OBSERVABILITY_BUILDERS` に登録されたまま**）。単一ソースは `shrink_freeze.CULLED_OBSERVABILITY_SECTIONS`。淘汰した事実は `display_cull` の 1 行 meta として必ず surface する（silence != evaluated）。環境変数 `EVOLVE_SHOW_CULLED=1` で一時的に全表示へ戻せる。

## 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン |
| フィードバック | reflect, report-feedback | reflect=修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映。report-feedback=evolve/audit レポートを LLM メタレビュー → evolve-anything 自身への改善 issue を todoroki-godai/evolve-anything に半自動起票（決定論 evolve_introspect が拾えない「読んで気づく」改善が対象。旧 feedback スキルの後継） |
| 直接パッチ最適化 | optimize, evolve-loop, generate-fitness, evolve-fitness | corrections/context → LLM 1パスパッチ → regression gate（`scripts/lib/regression_gate.py` に共通化）+ GEPA 数値ガードレール（入力/パッチ char 上限・実データ dry-run 較正・#120） |
| **fleet 観測・介入** | fleet (`bin/evolve-fleet`) | 全 PJ 横断で env_score / 導入状況を一覧表示。`status` / `tokens` / `test-guard status`（no-llm-in-tests / pytest-no-llm 導入状況）/ `discover` / `recall`（全 PJ memory を keyword 横断検索、決定論・LLM 非依存）/ `plugins`（インストール済み CC プラグインの最新性診断 — update/drift/unknown を決定論検出。version 無しプラグインの silent stale を cache↔marketplace source の差分で検出）/ `queue`（学習素材ベースで「今 evolve すべき PJ」を決定論・ゼロ LLM で列挙 — weak 未処理 + 新規 corr の合算が閾値以上の PJ・#79）/ `propose`（queue 待ち PJ に evolve --dry-run 提案をバッチ生成し集約レポート化・llm-batch-guard 承認ゲート付き・#81）/ `pr-start`/`pr-finish`（承認済み evolve 提案を worktree 隔離で commit→push→PR 化。適用そのものは対話 evolve のまま人間が行い、外殻の worktree 準備と push/PR だけを自動化。マージは常に人間・#82） |
| daily-evolve 入口 | queue | 全 PJ 横断の evolve 待ち一覧を表示し上から対話 evolve するガイド（pull 型・ADR-050 手動運用入口）。`evolve-fleet queue` の薄いラッパー（read-only・ゼロ LLM）+ 次アクション提示。`/cd <PJ>`→`/evolve-anything:evolve` の導線。CC 起動後タイミングの良い日に手で叩く想定（#80 launchd 自動登録の代替手段） |
| モデルティア変更 | tier | `bin/evolve-tier`（#193）の対話 UX ラッパー。現状表示（`show`）→ ユーザー発話から tier/model/effort を解釈（曖昧なら `AskUserQuestion`）→ `set` で正典更新 → `sync` の dry-run diff を全件提示 → **明示承認後にのみ** `sync --apply` → `drift` advisory 表示、の順でモデルティア正典を安全に変更。スキル自体はファイルを直接編集せず全変更は CLI 経由 |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・新規作成・削除候補 |
| セカンドオピニオン | second-opinion | codex CLI 検出時は外部 cold-read ルートB、それ以外は Claude Agent ルートA によるセカンドオピニオン |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| 構造化実装 | implement | plan artifact → タスク分解 → 実装（single/parallel）→ 検証 → テレメトリ記録 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存ツール。類似 dedup / 普遍性分類（universal/project/instance + 汎用度1-5）/ 三段階開示の配布版(Top-N)生成 / 記録↔分類↔配布の同期ゲート。判断は agent、決定論処理は `scripts/pitfall_curate.py`。`pitfall_manager`（自己進化専用）とは別物 |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（branches / worktrees / tmp dirs / Issues / Test plan 残件）を候補提示→個別承認→実行。tmp dir default prefix は `evolve-anything-` のみに安全側限定 |
| ユーティリティ | update, version | 更新・バージョン確認（backfill は #215 で CLI 削除→evolve 自動 ingest に統合、スキルは廃止リダイレクトのみ。旧 feedback スキルは report-feedback に統合し削除） |

## コンポーネント

各コンポーネントの設計経緯・根拠・issue/ADR 参照を含む詳細は **[spec/components.md](spec/components.md)**（SoT）。
ここは 1 行サマリのみ。**新コンポーネント追加・変更時は spec/components.md に詳細を書き、この表には 1 行だけ追記する（サマリは「何をするか1文 + 契約フラグ」で構成し目安 ≤130 字。`凍結`/`reject`/`dry-run`/`fail-open`/`人間承認`/`単一ソース`等の動作を縛る語は要約時も必ず残す）。**
**契約フラグを省略してよいかの判断基準**（cold に書いてあるかは基準にしない。**コンポーネント単位でなく不変条件単位**で判定する）: **その不変条件を全 write/遷移入口が必ず経由し、例外モード（warn 降格・fail-open・例外口）を含め常に reject する場合のみ、その条件は省略可**（例: `shrink_freeze.assert_no_new_keys` の凍結中新設 reject。降格経路なし）。**抜け道が1つでもある不変条件・通常ロジックやテストのみで守られている契約は hot に必ず残す**（例: `store_write` barrier 自身の未登録ストア reject も env `EVOLVE_WRITE_GUARD=warn` で降格できるため対象外／関数の単一ソース・TTL の read 時導出・dry-run 純度）。

| コンポーネント | 一言サマリ | 実体 |
|----------------|-----------|------|
| Observe hooks (24個 registered) | LLM コストゼロで使用・エラー・修正・ワークフロー・ファイル変更を自動記録 | `hooks/` |
| Auto Trigger | corrections 蓄積・セッション終了等で evolve/audit を自動提案 | `trigger_engine.py` |
| `userConfig` | trigger 閾値・各種上限など 21 項目をプラグイン有効化時に設定可能 | manifest |
| `genetic-prompt-optimizer` | corrections/context ベースの LLM 1パス直接パッチ + GEPA 数値ガードレールで暴走を抑制 | agent |
| `evolve-loop-orchestrator` | ベースライン→バリエーション→評価→人間確認のループ統合 | agent |
| `variant_generation` | バリエーション生成を direct import 方式に修理（旧 subprocess 経路を廃止） | `skills/evolve-loop-orchestrator/scripts/variant_generation.py` |
| `selection_reeval` | 採用前再評価で winner's curse を補正 | `skills/evolve-loop-orchestrator/scripts/selection_reeval.py` |
| `loop_ablation` | 設計文脈 vs naive 生成比較の opt-in 較正実験 | `scripts/lib/loop_ablation_stats.py` + `skills/evolve-loop-orchestrator/scripts/loop_ablation.py` |
| `evolve-scorer` | オーケストレーター + 3並列サブエージェントで3軸採点 | agent |
| `skill-triage` | CREATE/UPDATE/SPLIT/MERGE/OK の5択判定 | `skill_triage.py` |
| `tool_usage_analyzer` | セッション JSONL からツール呼び出しを抽出・分類し rule/hook 候補を生成 | `scripts/lib/tool_usage_analyzer/` |
| `trigger-eval-generator` | sessions+usage → skill-creator 互換 evals.json 自動生成 | `trigger_eval_generator.py` |
| `evolve-skill` | 自己進化パターン（Pre-flight / pitfalls.md）のピンポイント組み込み | skill |
| `agent-brushup` | エージェント定義の品質診断・改善提案・model exact-ID pin 検出・ask-before-fallback 検査 | `agent_quality.py` |
| `critical-instruction-compliance` | critical 行抽出+リフレーズ+違反検出+pitfall 自動学習 | `critical_instruction_extractor.py` |
| `second-opinion` | cold-read セカンドオピニオン（3モード）。codex 検出時は外部ルートBも選択可 | skill + agent |
| `growth-level` | env_score → Lv.1-10 + 日英称号マッピング | `growth_level.py` |
| `optimize_history_store` | accept/reject 履歴の正準ストア（PJ スコープ・worktree 安全 slug） | `optimize_history_store.py` |
| `evolve_decisions` | run envelope で並行 run を分離し未判断は deferred 保持。marker 書込失敗は `marker_error` で surface。supersede は対象パス単位、flat `result_path` は run 1件時のみ | `evolve_decisions.py` |
| `file_lock` | ファイル単位排他ロックと atomic write の単一ソース。ロック下からは `_locked` 版を使い自己 deadlock を回避 | `rl_common/file_lock.py` |
| `evolve_decision_ids` | 提案 identity `(repo_id, repo相対path, before_sha)` と判断イベント identity を隣接定義する純関数 module（取り違え防止） | `evolve_decision_ids.py` |
| `evolve_revert` | 採用した skill diff を戻す apply engine。3分岐（normal/冪等/conflict）で conflict は上書きせず中止、CLI は既定 dry-run・`--apply` のみ実書込。`--list` で一覧 | `evolve_revert/` + `evolve_revert_listing.py` + `bin/evolve-revert` + `evolve_revert_cli.py`（ADR-053/ADR-054） |
| optimize_history の effective view | revert 済み accept を判断母集団から畳む `fold_effective` が単一ソース。業務 reader は `load_effective_history`、raw は allowlist 3件のみ | `optimize_history_store.py` |
| `raw_history_gate` | raw history read を AST で閉じた allowlist に固定。未許可の新規呼出しも allowlist の消失（`stale_allowlist`）も fail。許可の単一ソースは production 定数 | `raw_history_gate.py` |
| `evolve_reconcile` | skill_evolve↔archive 矛盾の reconcile + batch_skip の observability 昇格 | `evolve_reconcile.py` |
| `token_usage_store/ingest/query` | PJ 別 LLM トークン消費の DuckDB SoR / 取り込み / 集計 | `token_usage_*.py` |
| `auto_memory_runner/broker` | auto-memory の enqueue（ゼロ LLM）+ 2相生成・書込。project スコープ4層防御で他PJ混入を reject、purge ツールは dry-run 既定 | `auto_memory_*.py` |
| `meta_quality` | スキル追加前の品質フィルタ（CREATE/REVIEW/SKIP） | `meta_quality.py` |
| `triage_ledger` | SKIP 判断の状態管理（TTL 45日・再発昇格・dry-run 非書込） | `triage_ledger.py` |
| `constraint_decay` | セッション後半に集中する correction の decay 検出 | `discover/patterns.py` |
| `negative_transfer` | スキル追加前後の success delta 計測 + 更新コンポーネント別帰属 | `audit/usage.py` |
| `eval_saturation` | trigger eval の飽和兆候診断 | `eval_saturation.py` |
| `subgoal_scorer` | BES 後ろ向き分解 — 5 サブゴール中間フィードバック | `subgoal_scorer.py` |
| `evolution_operators` | BES 前向き進化探索の決定論演算子 | `evolution_operators.py` |
| `memory_trace` | episodic 検索エラーの3類型帰属 | `memory_trace.py` |
| `slop_detector` | AI slop 日英 10 パターンの決定論検出 | `slop_detector.py` |
| `skill_extractor` | 成功軌跡採掘→スキル候補生成 + 4軸分解 + 3層ノイズ除去 + 失敗ロールアウトのマイニング | `skill_extractor/` |
| `skill_rm` | スキル軸の異種基準統一報酬 — 3軸射影で横断評価 | `fitness/skill_rm.py` |
| pitfall 自動強制 | pitfalls.md の編集時 lint + commit ゲート（オプトイン）。danger 判定は commit をブロック | `pitfall_registry.py` + `pitfall-curate/scripts/parse.py` + `genetic-prompt-optimizer/scripts/optimize_core.py` |
| `agent_team` | エージェント間の役割重複・孤立の決定論検出 | `agent_team.py` |
| observability contract | 必ず surface すべき observability 行の単一ソース（markdown/構造化 両経路） | `audit/observability.py` |
| advisory section 共通枠 | observability section の header/trailer 規約を単一化する共通 helper。20個の builder が経由 | `audit/advisory.py` |
| `advisory_proposals` | detector 結果を副作用なしで decision lane 用 proposal に変換する adapter registry | `advisory_proposals.py` |
| `advisory_decision_log` | advisory 提案の emit→drain lane での accept/reject を専用ストアに記録。optimize_history とは別ストアに分離 | `advisory_decision_log.py` + `audit/sections_advisory_decisions.py` |
| `evolve_introspect` | evolve result の自己解析→issue 候補生成（3カテゴリ） | `evolve_introspect/`（#122 で detectors/render/dedup/helpers に分割・re-export） |
| `evolve_result_schema` | result JSON の正準スキーマ契約 — impl/doc 両 drift 検出 | `evolve_result_schema.py` |
| `evolve_consistency` | P1 invariant の runtime self-detect（型 drift のみ） | `evolve_consistency.py` |
| `hook_drift` | 他ツール追従 hook の陳腐化検出（stale_pin + dead_ref、FP guard 付き） | `hook_drift.py` |
| `data_dir_migration` | DATA_DIR hook/tool 分裂の一元化 migration。marker 済みでも再分裂を再警告 | `data_dir_migration.py` |
| `spec_trigger` | 仕様未更新マージの SessionStart 検出→spec-keeper 提案 | `spec_trigger.py` |
| `capture_rate` | correction capture 率を決定論算出し audit に advisory surface | `capture_rate.py` |
| `orphan_store` | writer あり reader なしの jsonl ストアを決定論検出 | `orphan_store.py` |
| `store_registry` | ストア新設の事前契約ゲート — writer/reader/retention 宣言の機械可読 SoT。`status`（active/legacy/dead）が write 許可を制御 | `store_registry.py` |
| `store_write` write barrier | 全ストア書込の単一ゲート。store_registry の active 登録外は既定 reject、registry 不在は fail-open（例外口 `store_write_raw`） | `rl_common/store_write.py` |
| `outcome_metrics` | 行動アウトカム3軸（correction 再発率/一発成功率/rework率）を advisory 表示。再発率はA5で飽和ゲート追加（全type recurring+rate>=0.9→saturated） | `audit/outcome_metrics.py` |
| `utterance_archive` | 全PJ human 発話の恒久アーカイブ utterances.db（extractor/store/ingest/query） | `utterance_archive/` |
| `outcome_attribution` | outcome 3軸を per-skill 帰属し evolve ターゲットランキングへ自動入力。負の転移は末尾 rollback、dry-run に before/after 順位差分を surface | `audit/outcome_attribution.py` |
| `weak_signals` | 暗黙修正シグナルの決定論検出→weak_signals.jsonl レーン。reflect 確認後に corrections へ昇格。45日 TTL は read 時 age 導出で writer-death 非依存 | `weak_signals/` |
| `correction_semantic` | correction capture の二層化。utterances.db の発話を Haiku がバッチ意味判定（A5でcategory8値も同時付与）し weak_signals へ隔離。フェーズ昇格は human-source のみ駆動 | `correction_semantic/` |
| `bootstrap_backlog` | 初回 evolve で weak_signals バックログの消化方式を AskUserQuestion 3択で選ぶ bootstrap phase | `correction_semantic/bootstrap_backlog.py` |
| `judge_runner` / `safe_llm_call` | llm_judge の意味判定を daily runner の非対話実行へ移設。無人呼び出しは `safe_llm_call` に一点集約し4重防御、費用は呼び出し直前に事前予約 | `correction_semantic/judge_runner.py` + `safe_llm_call.py` |
| `daily_review` | evolve の「今日の修正確認」phase — 新規 weak_signal を最大5件 y/n 確認し promote 成功後のみ既読追記（部分失敗は対象外） | `correction_semantic/daily_review.py` |
| `review_channels` | y/n 確認に出す weak チャネルの単一ソース。content-rich チャネルのみ対象 | `correction_semantic/review_channels.py` |
| `idiom_autopromote` | confirmed idiom の再発 weak_signal を機械昇格。**#379 Step1 で凍結中、`autopromote()` は no-op** | `correction_semantic/idiom_autopromote.py` |
| `measurement_bug` | 複数 PJ の非自明な集計値が bit-exact 一致したら測定バグ候補として advisory surface | `audit/measurement_bug.py` |
| `growth_report` | evolve レポート末尾に成長状態を決定論表示 — あと N 件で次フェーズ。閾値は growth_engine が単一ソース | `growth_report.py` |
| `results_board`（戦果ボード） | growth-journal harness 削除の置換成果物。optimize_history/correction_rate を直読みし戦果を決定論表示 | `results_board.py` |
| `correction_rate` | ADR-054 §7.2.1 柱3(a)「指摘率」+A5カテゴリ内訳。3ストア read 時 join・freeze cutoff・カバレッジ100%確定週のみ表示・k週連続ゲート | `correction_rate.py` |
| `outcome_promotion_readiness` | 重み昇格レディネスの4条件決定論判定。全 ✓ で「重み昇格を提案」 | `audit/outcome_promotion_readiness.py` |
| `predictive_validity` | 重み昇格レディネス第4条件 — in/out-of-sample の順位相関で予測妥当性を判定 | `audit/predictive_validity.py` |
| `reward_ema` | バッチ跨ぎ符号付き advantage の EMA 累積で通時の安定効果を判定 | `audit/reward_ema.py` |
| `subagent_traces` | subagent 内部軌跡ストア — tool error/やり直しを per-agent_type で advisory 表示 | `subagent_traces/` + `audit/sections_subagent_traces.py` |
| `subagent_noise` | subagents.jsonl の agent_type ノイズ内訳を advisory 分解表示。判定は `noise_agent_type_kind` が単一ソース | `audit/sections_subagent_noise.py` + `rl_common/detection.py` |
| `worker_takeoff` | subagent の「completed 報告」↔実際の完遂の意味的乖離を決定論検知 | `rl_common/detection.py`(`detect_takeoff_divergence`) + `audit/sections_takeoff.py` |
| `verbosity` | 回答冗長性の学習ループ。Haiku バッチ判定が weak_signals へ emit、auto-apply しない | `verbosity/` + `hooks/record_verbosity.py` + `audit/sections_verbosity.py` |
| `cross_pj_priority` | confirmed idiom の PJ 横断優先提示（提示のみ・自動承認しない） | `correction_semantic/cross_pj_priority.py` |
| `testpaths_coverage` | pytest 収集漏れの決定論検出 — testpaths 宣言と実 tests/ ツリーを静的突合 | `testpaths_coverage.py` |
| `doc_budget` | hot ドキュメントの byte 予算・セクション別予算・リンク実在突合を決定論検出 | `doc_budget.py` + `audit/sections_doc_budget.py` |
| `plugin_self` origin | プラグイン本体 repo 直下 skills/ を診断対象化。auto-apply は人間承認必須に降格 | `skill_origin.py` |
| `scaffold_advisory` | advisory 3点セット追加の scaffold — builder stub 生成 + 配線チェックリスト。CLI は既定 dry-run | `scaffold_advisory.py` |
| `dogfood gate` | 通し評価ゲート — 3層検査（dry-run 不変/report invariants/コードブロック実行）。`--layer light` は pre-push で非ブロッキング自動実行 | `scripts/lib/dogfood/`, `scripts/git-hooks/` |
| `sibling_copy_guard` | diff-scoped 兄弟コピー検出。pre-push に非ブロッキング警告配線 | `scripts/lib/sibling_copy_guard.py` + `scripts/git-hooks/pre-push.local` |
| `evolve-release-sync` | リリース後のローカルプラグイン自動同期。`tag --push` 直後に実行、`--dry-run` 対応 | `bin/evolve-release-sync` |
| `pj_slug` | PJ slug 導出の単一ソース。read/write 同一関数で worktree slug 食い違いを防止 | `pj_slug.py` + `hooks/restore_state.py` |
| weak_signals drain 永続化 | 決定論3チャネルの永続化を `evolve --drain` の apply 境界に配線。pending marker の dry-run 書込は意図された設計（消さない） | `weak_signals/batch.py` |
| reconcile_surfaced drain 永続化 | remediation 連続提示の count marker 書込と閾値到達時の自動却下を `evolve --drain` の apply 境界へ移設。phases の dry-run は `persist=False` で非書込 | `cli.py` + `_env.py` + `phases_remediate.py` |
| `idiom_filter` | 過汎用 idiom の FP guard — 3ゲートで confirmed→idiom_autopromote の FP 製造を遮断。SKILL.md の AskUserQuestion で idiom 単位拒否も可能 | `correction_semantic/idiom_filter.py` |
| `representative` | correction group の representative 品質改善 — user 発話のみ抽出・直前行動要約を添付 | `correction_semantic/representative.py` |
| remediation 参照リンク相対化 | separation emit prompt のマシン固有絶対パスを PJ ルート相対化 | `remediation/fixers_llm.py` |
| `multiview_eval` | evolve 提案を4視点（再利用可能/過学習疑い/退行リスク/コスト増）で決定論分類 | `audit/multiview_eval.py`, `audit/sections_multiview.py` |
| `relevance_gate` | 過去経験の提案を現在文脈との関連度でゲートし、無関係を理由付きで `suppressed` 分離 | `correction_semantic/relevance_gate.py` |
| `report-feedback` | evolve/audit レポートを LLM メタレビューし改善 issue を半自動起票 | `skills/report-feedback/`, 契約 `scripts/lib/tests/test_report_feedback_contract.py` |
| `paired_trajectory` | スキル使用群 vs 非使用群でアウトカム差を決定論対照集計（能動再実行なし） | `audit/sections_paired.py` + `audit/usage.py` |
| recall `[[link]]` 1-hop | `evolve-fleet recall` の芋づる想起 — fact 本文の `[[link]]` を1-hop 先まで加算 | `fleet/recall.py` |
| recall validity-aware ranking | stale/superseded memory を validity metadata で降格（ハード除外はしない） | `fleet/recall.py` |
| reinforce_memory 配線 | dead-code だった `reinforce_memory` を recall/SessionStart 注入時に本番配線（CLI opt-in で recall 純粋性維持） | `memory_temporal.py` + `fleet/recall.py` + `hooks/instructions_loaded.py` |
| temporal provenance 書込配線 | APEX-MEM の valid_from/source_correction_ids write 側配線を活性化。importance 採点前に発火（純加算、stale/superseded 非発火） | `memory_temporal.py` + `auto_memory_broker.py` + `hooks/session_summary.py` |
| subagents/errors 測定バグ修正 | subagents.jsonl の agent_type ノイズを writer/reader 二重防御（`is_noise_agent_type` 単一ソース）で遮断 + errors.jsonl の error_type unknown を決定論分類 | `hooks/subagent_observe.py` + `fleet/collectors.py` + `fanout_cost.py` + `hooks/stop_failure.py` + `rl_common/detection.py` |
| `memory_capability` | 記憶操作を read/use/write/maintain 観点で advisory 評価。memory dir 解決は `resolve_cc_memory_dir` が単一ソース | `scripts/lib/memory_capability.py` + `audit/sections_memory.py` + `pj_slug.resolve_cc_memory_dir` |
| `skill_vuln_scan` | 取り込みスキルの静的脆弱性スキャン — remote_exec/secret_exfil 等を combo 必須で検出 | `skill_vuln_scan.py` + `audit/sections_skill_vuln.py` |
| `fanout_cost` | fan-out 費用対効果の advisory section。advantage は各群≥5件の floor ゲート付き | `scripts/lib/fanout_cost.py` + `audit/sections_fanout.py` |
| `memory_contagion` | 評価源バイアスの記憶伝播を audit advisory で検出 | `audit/memory_contagion.py` |
| `memory_guard` | auto-memory 書込境界の runtime 記憶汚染検出。prompt_injection/secret_exfil を reject（検査失敗は fail-open）。同名エントリの上書きは決定論遷移検証でゲート | `memory_guard.py` + `auto_memory_broker.py` + `memory_capability.py` + `audit/sections_memory.py` |
| `fleet_queue` | 学習素材ベースの evolve 待ち PJ を決定論・ゼロ LLM で列挙 | `fleet/queue.py` + `fleet/queue_state.py` + `fleet/cli.py` + `fleet/collectors.py` + `fleet/formatters.py` |
| `queue_verify` | queue の verify 待ちを read 時純粋導出。新ストアは作らない | `fleet/queue_verify.py` |
| `fleet_detect` | 全 PJ 横断の決定論 weak_signals 検出。daily runner が毎朝蓄積 | `fleet/detect.py` + `bin/evolve-daily-run` |
| `daily` | 毎朝の evolve queue 自動実行 + SessionStart 通知。適用は対話で人間承認 | `scripts/lib/daily/` + `bin/evolve-daily-install` + `bin/evolve-daily-run` + `hooks/restore_state.py` |
| `icebox_notice` | daily runner の icebox 棚卸し気づきトリガー。fail-open で既存ファイル非破壊、閾値未満は無音 | `scripts/lib/daily/icebox_notice.py` + `bin/evolve-daily-run` + `hooks/restore_state.py` |
| `icebox_reconcile` | icebox 棚卸しを3レーン決定論分類（成立/観測器不在/失効候補） | `scripts/lib/icebox_reconcile.py` + `bin/evolve-daily-run` + `scripts/lib/audit/sections_icebox_reconcile.py` |
| `artifacts_hygiene` | artifact 衛生5検出器を observability に surface（決定論・LLM 非依存） | `audit/sections_artifacts.py` |
| `memory_hygiene` | memory dir 衛生3検出器。clean 時は非表示、重複残骸は手順提案のみで auto-apply しない | `memory_index_orphan.py` + `memory_schema_check.py` + `memory_dup_residue.py` + `audit/sections_memory.py` |
| `memory_stale_refs` | Memory Health の stale reference 誤検知（スラッシュ列挙の誤読）を修正 | `path_extractor.py` + `audit/memory.py` |
| `invalid_frontmatter` | 壊れた frontmatter で発火不能なスキルを直接 surface（auto-fix せず人手修正提案） | `frontmatter.py` + `effort_detector.py` + `audit/sections_invalid_frontmatter.py` |
| `self_contamination` | 自己汚染ハルシネーション指紋を transcript 走査で恒久計測（ゼロLLM・read-only） | `self_contamination_scan.py` + `audit/sections_self_contamination.py` |
| `evolve-tier` | モデルティア正典を一元化する CLI — set/sync[--apply]/drift の3コマンド。sync は既定 dry-run、`--apply` のみ書込 | `bin/evolve-tier` + `tier_policy.py` + `tier_policy_sync.py` + `tier_policy_drift.py` + `tier_policy_cli.py` |
| `evaluation_provenance` | 評価スコアに紐づく実行条件（model/effort/tool policy）の記録契約。envelope が単一ソース。不明値は推測せず None | `scripts/lib/evaluation_provenance.py` |
| `skill_reachability` | SKILL.md 宣言 callable が production コードから到達不能かを AST 静的解析で検出 | `skill_declaration_reachability.py` + `audit/sections_skill_reachability.py` + `dogfood/cli.py` |
| `fleet_propose` | queue 待ち PJ に `evolve --dry-run` を順次実行し提案を集約レポート化。承認ゲート付き、reject 済み提案は再提示しない | `fleet/propose.py` + `fleet/cli_propose.py` |
| `fleet_pr` | 承認済み evolve 提案を repo 外 worktree で commit→push→PR 化。path allowlist・push account guard で強制、マージは人間 | `fleet/pr.py` + `fleet/cli_pr.py` |
| `agent_coordination` | Claude Code primary／Codex opt-in の top-level executor lane 管理 | `agent_coordination/` + `bin/evolve-agent-task` + `docs/agent-contract/` |
| `codex_config_cleanup` | 既知4カテゴリの Codex 設定残骸を検出し復元先が一意な指紋だけ plan/apply | `agent_coordination/codex_cleanup.py` + `bin/evolve-codex-config-cleanup` |
| `runtime_telemetry` | usage/sessions/errors の hook record に `runtime=claude\|codex` を較正追加。**Codex hook 配線は保留** | `hooks/common.py` + 5 writer + `agent_coordination/runtime_summary.py` |
| `codex_usage` | codex CLI 利用状況を advisory 表示（fail-open）。CC 側 token_usage とは合算しない | `fleet/codex_usage.py` + `fleet/formatters.py` |

## クイックスタート

```
# 初回セットアップ（新規PJ導入時）
# observe hooks が自動でセッションを記録する。数セッション利用後に下記を回せばよい。
# （旧 /evolve-anything:backfill は #215 で CLI 削除済みの幻なので廃止）
bin/evolve-fleet ingest             # 全 PJ の human 発話を utterances.db に取り込み（任意・ゼロ LLM）

# 日次運用（全フェーズ一括 = 取り込み + 改善提案）
/evolve-anything:evolve

# 修正フィードバックの反映
/evolve-anything:reflect

# 特定スキルの自己進化パターン組み込み
/evolve-anything:evolve-skill my-skill

# 環境の健康診断
/evolve-anything:audit

# 全 PJ 横断の fleet ステータス
bin/evolve-fleet status

# PJ 別 LLM トークン消費の初期取り込み（直近 90 日）
bin/evolve-fleet tokens --backfill

# PJ 別 LLM トークン消費サマリ (TOP 3 + 異常)
bin/evolve-fleet tokens

# 全 PJ の memory を keyword 横断検索（決定論・LLM 非依存）
bin/evolve-fleet recall "duckdb checkpoint"
bin/evolve-fleet recall "認証 ルーティング" --json --limit 5

# インストール済み CC プラグインの最新性診断（update/drift/unknown を決定論検出）
bin/evolve-fleet plugins
bin/evolve-fleet plugins --json

# 全 PJ の学習素材（決定論 weak_signals）を検出・蓄積（#304・ゼロ LLM・冪等）
bin/evolve-fleet detect                   # 直近セッションから検出（daily runner が毎朝自動実行）
bin/evolve-fleet detect --backfill        # 過去チャットを遡って取りこぼしを回収
bin/evolve-fleet detect --pj amamo --dry-run

# 学習素材ベースで「今 evolve すべき PJ」を列挙（決定論・ゼロ LLM）
bin/evolve-fleet queue                    # weak 未処理 + 新規 corr >= 閾値（既定5）の PJ をテーブル表示
bin/evolve-fleet queue --json --threshold 3
# 毎朝の evolve queue 自動実行を launchd に登録（#80・既定 09:00 / --time HH:MM / --uninstall）
bin/evolve-daily-install
bin/evolve-daily-install --uninstall

# advisory 3点セット追加の scaffold（module stub 生成 + 多点配線チェックリスト・#118）
bin/evolve-scaffold-advisory my_check                 # dry-run（stub + checklist 表示）
bin/evolve-scaffold-advisory my_check --with-store --write

# 採用した skill diff を戻す（#402・既定 dry-run。entry_id は戦果ボードか --list が印字する）
bin/evolve-revert --list                  # 戻せる採用の一覧（entry_id つき・read-only・#402 D2）
bin/evolve-revert <entry_id>              # 何が起きるか確認（書込ゼロ）
bin/evolve-revert <entry_id> --apply      # 実際に戻す
bin/evolve-revert <entry_id> --dump-before /tmp/before.md   # 戻さず変更前の本文だけ取り出す

# モデルティア正典の一元管理（#193）
bin/evolve-tier show                      # ティア表 + 正典ソース（file/defaults）を表示
bin/evolve-tier set HEAD --model sonnet --effort max   # 正典を更新（atomic write）
bin/evolve-tier sync                      # targets への反映を dry-run（diff 表示のみ）
bin/evolve-tier sync --apply              # drift のみ実書込（冪等）
bin/evolve-tier drift                     # 正典に無いモデルエイリアスの散文残存を検出

# モデルティアを対話的に変更（上記 CLI の対話 UX ラッパー・diff 提示+承認フロー付き）
/evolve-anything:tier

# エージェント品質診断
/evolve-anything:agent-brushup

# セカンドオピニオン（codex代替）
/evolve-anything:second-opinion

# SPEC.md の初期化・更新
/evolve-anything:spec-keeper init
/evolve-anything:spec-keeper update

# 孤立した依存プラグインのクリーンアップ
claude plugin prune
```

## 適応度関数

組み込み8個: `default`（LLM汎用評価）、`skill_quality`（ルールベース構造品質）、`coherence`（構造的整合性4軸）、`telemetry`（テレメトリ3軸）、`constitutional`（原則ベースLLM Judge評価 + /cso security軸）、`chaos`（仮想除去ロバストネス）、`environment`（coherence+telemetry+constitutional+skill_quality 動的重み統合、`config.py` で閾値集約）、`plugin`（evolve-anything 用プラグイン統合 fitness）。
プロジェクト固有: `scripts/rl/fitness/{name}.py` に配置 → `--fitness {name}` で使用。
環境スコア: `audit --coherence-score --telemetry-score --constitutional-score` で構造品質+行動実績+原則遵守の統合スコアを表示。

詳細は [README.ja.md](README.ja.md#適応度関数) を参照。

## evolve-scorer のドメイン自動判定

CLAUDE.md からドメイン（ゲーム/API/Bot/ドキュメント）を推定し評価軸を自動切替。
詳細は [README.ja.md](README.ja.md#evolve-scorer-のドメイン自動判定) を参照。

## Superpowers 共存

Superpowers プラグインがインストールされている場合、メタ操作時（evolve/audit/reflect/optimize/discover）は Superpowers の TDD/SDD/debugging スキルを発火させない。開発タスク時はフル活用する。

## Compaction Instructions

コンテキスト圧縮時、以下の情報をサマリーに必ず含めること:

1. **完了済みタスクと未完了タスクの区別** — 完了タスクを再実行しないこと
2. **呼び出されたスキルの実行結果** — 完了/未完了/エラーの状態
3. **変更したファイルの一覧** — パスと変更内容の要約
4. **ユーザーの最後の指示** — 次に何をすべきかの文脈

## テスト

```bash
cd <PLUGIN_DIR>
# bare コマンドで全件走る（pytest.ini の testpaths が収集パスを宣言済み。#468）。
# scripts/lib/tests（1111件）/ bin/tests も含む。パス列挙は不要かつ取りこぼしの温床なので避ける。
python3 -m pytest -v

# プラグイン定義の整合性チェック
claude plugin validate
```

フルスイートはデフォルトで全件実行する（slow マーカーによる deselect は無し）。
収集パスは `pytest.ini` の `testpaths` が単一ソース。新しい tests/ を足したら testpaths に追記する
（漏れは audit の Testpaths Coverage チェック = `scripts/lib/testpaths_coverage.py` が検出する。#468）。
pytest-xdist `-n auto` で並列実行（`pytest.ini` の `addopts` に設定済み）、2026-06-12 時点で約 32 秒・4972件（直列だと約 135 秒）。#457 で run_evolve 系の実環境ストア読みを隔離し直列 32 分→1 分→xdist で約 32 秒に短縮。**並行 worker に回させるときは `-n 0` で直列**（targeted テストまで多プロセス化し CPU 飢餓するため）。

`test_evolve_keyset_snapshot.py` は evolve-anything 自身の実スキル構成に dry-run するため、計測窓 suppress の暦日境界等で regression でないのに出たり消えたりするキーがある。`fixtures/evolve_keyset_optional.txt` に条件付き透明化キーの prefix を宣言し、宣言済み prefix の増減のみ許容する二層 golden 方式（#209）。`UPDATE_SNAPSHOTS=1` は golden 上書きでなく既存キーとの union merge（条件付きキーを golden から消さない）。

リリース前は `bin/evolve-dogfood-gate --layer all` も全緑を確認する（pytest が掬えない実環境の繋ぎ目
— dry-run 不変 / report invariants / SKILL.md コードブロック — を検査する。#496）。フル `all` は
Layer1b の drain が重く約3.5分かかる。日常 push は **`--layer light`**（Layer1a 不変 + Layer2 +
Layer3、約十数秒。重い Layer1b drain と ingest E2E を除外）が `pre-push` hook 経由で**非ブロッキング
警告**として自動実行される。hook ソースは `scripts/git-hooks/pre-push.local`、導入は
`bash scripts/git-hooks/install.sh`（gstack-redact の managed pre-push が chain する `pre-push.local`
へコピー。共有 hooks なので worktree 横断で1回でよい）。

**HOME 隔離は root conftest の autouse が全テストへ自動適用する（#119・旧 #457）。** `run_evolve` は
`project_dir=tmp_path` でも後段フェーズ（utterance ingest / prune global check /
weak_signals / correction_semantic）が `Path.home()/.claude/projects`（実環境 ≈9925 jsonl /
1.9GB）を default 走査するため、未隔離だと 1 件数十秒に膨張する。以前は
`skills/evolve/scripts/tests/` の conftest autouse と各テストの手動
`from test_home_isolation import isolate_home` 頼みで「隔離を知らないと膨張する罠」が残っていた
（#457）。#119 で root `conftest.py` の autouse（`isolate_home` を single source から import）へ
昇格し、**全 testpath を一律に隔離する**（新規テストは何もしなくても隔離される）。隔離 HOME は
test の `tmp_path` の外（`tmp_path_factory` 側）に作る（`tmp_path` を列挙する fleet
enumerate / does-not-write 系を汚染しないため）。実 `~/.claude` を読む必要があるテスト
（live API bench / 実 PJ ingest）は `@pytest.mark.real_home`（または `bench` / `bench_ingest`）で
opt-out する。ルート conftest の `CLAUDE_PLUGIN_DATA`(=DATA_DIR) 隔離は `Path.home()` 由来パスには
効かないため、HOME 隔離はこの autouse が担う。

## Specification
- 現在の仕様全体像: [SPEC.md](SPEC.md)
- コンポーネント詳細（設計経緯・issue/ADR 参照の SoT）: [spec/components.md](spec/components.md)
- 用語集（Ubiquitous Language）: [CONTEXT.md](CONTEXT.md) — PJ 固有 jargon を 1 語で decode。鮮度は `scripts/lib/glossary_drift.py` が検出し spec-keeper update が advisory 提示。新概念を入れたら CONTEXT.md に 1 行追記する
- 詳細仕様: [spec/](spec/)
- 設計判断の記録: [docs/decisions/](docs/decisions/)
