# evolve-anything Plugin

スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化**、**fleet 観測・介入** を提供する Claude Code Plugin。

## 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン |
| フィードバック | reflect, report-feedback | reflect=修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映。report-feedback=evolve/audit レポートを LLM メタレビュー → evolve-anything 自身への改善 issue を todoroki-godai/evolve-anything に半自動起票（決定論 evolve_introspect が拾えない「読んで気づく」改善が対象。旧 feedback スキルの後継） |
| 直接パッチ最適化 | optimize, evolve-loop, generate-fitness, evolve-fitness | corrections/context → LLM 1パスパッチ → regression gate（`scripts/lib/regression_gate.py` に共通化） |
| **fleet 観測・介入** | fleet (`bin/evolve-fleet`) | 全 PJ 横断で env_score / 導入状況を一覧表示。`status` / `tokens` / `test-guard status`（no-llm-in-tests / pytest-no-llm 導入状況）/ `discover` / `recall`（全 PJ memory を keyword 横断検索、決定論・LLM 非依存）/ `plugins`（インストール済み CC プラグインの最新性診断 — update/drift/unknown を決定論検出。version 無しプラグインの silent stale を cache↔marketplace source の差分で検出）/ `queue`（学習素材ベースで「今 evolve すべき PJ」を決定論・ゼロ LLM で列挙 — weak 未処理 + 新規 corr の合算が閾値以上の PJ・#79） |
| daily-evolve 入口 | queue | 全 PJ 横断の evolve 待ち一覧を表示し上から対話 evolve するガイド（pull 型・ADR-050 手動運用入口）。`evolve-fleet queue` の薄いラッパー（read-only・ゼロ LLM）+ 次アクション提示。`/cd <PJ>`→`/evolve-anything:evolve` の導線。CC 起動後タイミングの良い日に手で叩く想定（#80 launchd 自動登録の代替手段） |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・新規作成・削除候補 |
| セカンドオピニオン | second-opinion | Claude Agent による独立した cold-read セカンドオピニオン（codex 代替） |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| 構造化実装 | implement | plan artifact → タスク分解 → 実装（single/parallel）→ 検証 → テレメトリ記録 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存ツール。類似 dedup / 普遍性分類（universal/project/instance + 汎用度1-5）/ 三段階開示の配布版(Top-N)生成 / 記録↔分類↔配布の同期ゲート。判断は agent、決定論処理は `scripts/pitfall_curate.py`。`pitfall_manager`（自己進化専用）とは別物 |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（branches / worktrees / tmp dirs / Issues / Test plan 残件）を候補提示→個別承認→実行。tmp dir default prefix は `evolve-anything-` のみに安全側限定 |
| ユーティリティ | update, version | 更新・バージョン確認（backfill は #215 で CLI 削除→evolve 自動 ingest に統合、スキルは廃止リダイレクトのみ。旧 feedback スキルは report-feedback に統合し削除） |

## コンポーネント

各コンポーネントの設計経緯・根拠・issue/ADR 参照を含む詳細は **[spec/components.md](spec/components.md)**（SoT）。
ここは 1 行サマリのみ。**新コンポーネント追加・変更時は spec/components.md に詳細を書き、この表には 1 行だけ追記する。**

| コンポーネント | 一言サマリ | 実体 |
|----------------|-----------|------|
| Observe hooks (23個 registered) | LLM コストゼロで使用・エラー・修正・ワークフロー・ファイル変更を自動記録 | `hooks/` |
| Auto Trigger | corrections 蓄積・セッション終了等で evolve/audit を自動提案 | `trigger_engine.py` |
| `userConfig` | trigger 閾値・各種上限など 18 項目をプラグイン有効化時に設定可能 | manifest |
| `genetic-prompt-optimizer` | corrections/context ベースの LLM 1パス直接パッチ | agent |
| `evolve-loop-orchestrator` | ベースライン→バリエーション→評価→人間確認のループ統合 | agent |
| `evolve-scorer` | オーケストレーター + 3並列サブエージェントで3軸採点 | agent |
| `skill-triage` | CREATE/UPDATE/SPLIT/MERGE/OK の5択判定 | `skill_triage.py` |
| `trigger-eval-generator` | sessions+usage → skill-creator 互換 evals.json 自動生成 | `trigger_eval_generator.py` |
| `evolve-skill` | 自己進化パターン（Pre-flight / pitfalls.md）のピンポイント組み込み | skill |
| `agent-brushup` | エージェント定義の品質診断・改善提案・upstream 監視・model exact-ID pin 検出（#449）+ addyosmani skill anatomy 欠落節の根拠付き改善提案（#63） | `agent_quality.py` |
| `critical-instruction-compliance` | critical 行抽出+リフレーズ+違反検出+pitfall 自動学習 | `critical_instruction_extractor.py` |
| `second-opinion` | cold-read セカンドオピニオン（3モード、codex 不要） | agent |
| `growth-level` | env_score → Lv.1-10 + 日英称号マッピング | `growth_level.py` |
| `optimize_history_store` | accept/reject 履歴の正準ストア（PJ スコープ・worktree 安全 slug）[ADR-031] | `optimize_history_store.py` |
| `evolve_decisions` | evolve 提案の accept/reject を emit→drain 2相で決定論キャプチャ（`evolve --drain`）[ADR-041, #400, #402] | `evolve_decisions.py` |
| `evolve_reconcile` | skill_evolve↔archive 矛盾の reconcile + batch_skip の observability 昇格（#400） | `evolve_reconcile.py` |
| `token_usage_store/ingest/query` | PJ 別 LLM トークン消費の DuckDB SoR / 取り込み / 集計 | `token_usage_*.py` |
| `auto_memory_runner/broker` | auto-memory の enqueue（ゼロ LLM）+ 2相生成・書込 [ADR-037] | `auto_memory_*.py` |
| `meta_quality` | スキル追加前の品質フィルタ（CREATE/REVIEW/SKIP） | `meta_quality.py` |
| `triage_ledger` | SKIP 判断の状態管理（TTL 45日・再発昇格・dry-run 非書込）（#308） | `triage_ledger.py` |
| `constraint_decay` | セッション後半に集中する correction の decay 検出 | `discover/patterns.py` |
| `negative_transfer` | スキル追加前後の success delta 計測 + 更新コンポーネント別帰属（#288） | `audit/usage.py` |
| `eval_saturation` | trigger eval の飽和兆候診断（#292） | `eval_saturation.py` |
| `subgoal_scorer` | BES 後ろ向き分解 — 5 サブゴール中間フィードバック（#253） | `subgoal_scorer.py` |
| `evolution_operators` | BES 前向き進化探索の決定論演算子（#256） | `evolution_operators.py` |
| `memory_trace` | episodic 検索エラーの3類型帰属（#254） | `memory_trace.py` |
| `slop_detector` | AI slop 日英 10 パターンの決定論検出（#255） | `slop_detector.py` |
| `skill_extractor` | 成功軌跡採掘→スキル候補生成 + 4軸分解 + 3層ノイズ除去（#291, #381, #387）+ failure-rollout マイニング + 失敗の罠軸 (#27) | `skill_extractor/` |
| `skill_rm` | スキル軸の異種基準統一報酬 — 3軸射影で横断評価（#304） | `fitness/skill_rm.py` |
| pitfall 自動強制 | pitfalls.md の編集時 lint + commit ゲート（オプトイン）[ADR-027] | `pitfall_registry.py` |
| `agent_team` | エージェント間の役割重複・孤立の決定論検出（#326） | `agent_team.py` |
| observability contract | 必ず surface すべき行の単一ソース（markdown/構造化 両経路）[ADR-028] | `audit/observability.py` |
| `evolve_introspect` | evolve result の自己解析→issue 候補生成（3カテゴリ）[ADR-033, ADR-034] | `evolve_introspect.py` |
| `evolve_result_schema` | result JSON の正準スキーマ契約 — impl/doc 両 drift 検出（#375, #379） | `evolve_result_schema.py` |
| `evolve_consistency` | P1 invariant の runtime self-detect（型 drift のみ）（#377-5） | `evolve_consistency.py` |
| `hook_drift` | 他ツール追従 hook の陳腐化検出（stale_pin + dead_ref: flow-chain 参照スキルの実在突合、正規化 FP guard 付き #316）[ADR-036] | `hook_drift.py` |
| `data_dir_migration` | DATA_DIR hook/tool 分裂の一元化 migration（marker ゲート redirect + DuckDB rebuild マージ、`evolve-fleet migrate-data`）[#364, ADR-042] | `data_dir_migration.py` |
| `spec_trigger` | 仕様未更新マージの SessionStart 検出→spec-keeper 提案 [ADR-044] | `spec_trigger.py` |
| `capture_rate` | correction capture 率（20+ ターン session のうち correction 検出割合）を決定論算出し audit に advisory surface（#421） | `capture_rate.py` |
| `orphan_store` | writer あり reader なしの jsonl ストアを決定論検出（hooks=writer / scripts+skills=reader 静的突合）（#422） | `orphan_store.py` |
| `store_registry` | ストア新設の事前契約ゲート — writer/reader/retention 宣言の機械可読 SoT（jsonl/db 両対応、writer_locus で batch 書込を stale 突合から除外。`status` active/legacy/dead で write 許可を制御 #55）（#430-#434, #55） | `store_registry.py` |
| `store_write` write barrier | 全ストア書込の単一ゲート — `store_write(store_name, record)` が canonical `DATA_DIR/<name>` を内部解決し store_registry の active 登録を runtime guard で照合。**既定 reject**（Phase 2b で全 production caller=hooks 10 + scripts/lib 6 を移行完了 → warn-only から昇格・登録外書込は `StoreWriteError`／緊急避難は env `EVOLVE_WRITE_GUARD=warn`／store_registry 不在は fail-open／不正 guard 値は warn へ de-escalate）。例外口は別名関数 `store_write_raw`。read（union 寛容）と write（厳格）を分離し共有は store_registry のみ [ADR-049, #55] | `rl_common/store_write.py` |
| `outcome_metrics` | 行動アウトカム3軸（correction 再発率 / 一発成功率 / rework 率近似）を advisory 表示。utilization の plugin レイアウト探索修理も同梱 [#423, ADR-046] | `audit/outcome_metrics.py` |
| `utterance_archive` | 全PJ human 発話の恒久アーカイブ utterances.db（extractor/store/ingest/query）。物理PK+論理UNIQUEで resume 複製を弾く・cwd 由来 pj_slug・evolve/audit batch + evolve-fleet ingest + SessionStart staleness advisory（#430） | `utterance_archive/` |
| `outcome_attribution` | outcome 3軸（一発成功率 / rework 率 / correction 再発率）を per-skill 帰属し evolve ターゲットランキングへ自動入力 + negative_transfer gate で退行スキルを末尾 rollback（#10）+ RODS reward 分散列（#28）+ reward EMA 列（バッチ跨ぎ符号付き advantage・#64）。dry-run に before/after 順位差分を surface [#433, #10, #28, #64] | `audit/outcome_attribution.py` |
| `weak_signals` | 暗黙修正シグナルの決定論検出（直後手編集 / permission deny / 言い直し / Esc 中断）→ weak_signals.jsonl レーン。corrections 直入れせず reflect 確認後に昇格。言い直し閾値は実コーパス dry-run で決定（jaccard 0.8）。45日 TTL で期限切れを expired マークし昇格候補から除外（#442）+ observability に evolve 昇格誘導文言（#444）[#432] | `weak_signals/` |
| `correction_semantic` | correction capture の二層化（#431）。utterances.db の dialogue 発話を Haiku がバッチ意味判定（auto_memory 2 相と同型）→ weak_signals(channel=llm_judge) 隔離 + 個人辞書（correction_idioms.jsonl）。フェーズ昇格は human-source のみ駆動（provenance_weight）/ reflect 昇格フロー（--show-weak-signals / --promote-weak）[#431] | `correction_semantic/` |
| `bootstrap_backlog` | 初回 evolve で既存 weak_signals バックログの消化方式を AskUserQuestion 3 択で選ぶ bootstrap phase（marker で1回きり・slug スコープ厳守・常時 emit）（#443） | `correction_semantic/bootstrap_backlog.py` |
| `daily_review` | evolve の「今日の修正確認」phase — 新規 weak_signal を idiom 単位 group 化し最大5件を y/n 確認、promote 成功後のみ既読追記（既読ストア correction_review_seen.jsonl）（#446） | `correction_semantic/daily_review.py` |
| `idiom_autopromote` | confirmed idiom と同テキストの再発 weak_signal を機械昇格（照合は pj_slug × idiom テキスト単位）。安全弁3つ: daily_cap / observability 常時 surface / `evolve-reflect --revoke-idiom` 巻き戻し（#447, ADR-047）。confirmed 化の正準経路は `evolve-reflect --promote-weak` の confirm 配線（#463） | `correction_semantic/idiom_autopromote.py` |
| `measurement_bug` | 複数 PJ で非自明な集計値が bit-exact 一致したら測定バグ候補として advisory surface（≥3 PJ・0/None は構造的に除外）（#445, #185） | `audit/measurement_bug.py` |
| `growth_report` | evolve レポート末尾に成長状態を決定論表示 — あと N 件で次フェーズ / 今日の昇格成果。閾値は growth_engine の定数が単一ソース（#448） | `growth_report.py` |
| `outcome_promotion_readiness` | ADR-046 重み昇格レディネスの4条件決定論判定（分散 / 件数下限 / 方向妥当性 / 予測妥当性#42）— ✓✗ + evidence で advisory surface、全 ✓ で「重み昇格を提案」。session 系分母は session_store union read（db read_only + 未 ingest jsonl）で実効化済み。条件3 は同一 apply の二重 anchor を `(pj,axis,before,after)` dedup で非独立証拠の二重計上から救出（#77）（#461, #469, #42, #77） | `audit/outcome_promotion_readiness.py` |
| `predictive_validity` | 重み昇格レディネスの第4条件（#42）— skill_activations×sessions の per-skill 一発成功率を ts 中央値で in/out-of-sample 分割し、共通出現 skill（≥5）の順位を純Python Spearman で相関。rho≥0.5 で pass、insufficient_data は「データ不足」明示で捏造せず保守的に昇格ブロック（誤昇格抑制）。集計平均順位の分布外転移を検出（arXiv 2606.19704） | `audit/predictive_validity.py` |
| `reward_ema` | バッチ跨ぎ符号付き advantage の EMA 累積（MAA・#64・arXiv 2606.20475）。各スキルの baseline 比 一発成功率差（advantage）を evolve サイクル跨ぎで符号付き EMA（α=0.3）累積し「通時で安定して効くか」を RODS（#28・単一スナップショット分散）と相補的に区別。新ストア `reward_ema.jsonl`（store_registry active・`writer_locus="batch"`・pj_slug で read 側照合）。読み=phases_diagnose→`apply_outcome_ranking(reward_ema=)` の advisory 列（順位非影響・dry-run 安全）、書き=`evolve --drain` の apply 境界のみ（plant-the-seed・3-4 サイクルから有意）。magnitude 閾値は捏造せず符号＋サイクル数のみ | `audit/reward_ema.py` |
| `subagent_traces` | subagent 内部軌跡ストア（#38）— subagents.jsonl の agent_transcript_path が指す transcript の tool_use/tool_result/is_error をパースし subagent が内部で何回 error してからやり直したかを per-agent_type で advisory 表示。親セッションの error_count しか見ない既存 outcome 帰属の盲点（内部 error 連発でも最終成功なら一発成功と誤記録）を塞ぐ。新ストア `subagent_traces.jsonl`（store_registry active・`writer_locus="batch"`・store_write barrier・pj_slug スコープ・agent_id last-append-wins）。読み=audit section（agent_type 別の内部一発成功率・floor3件・重み非干渉。低品質 ⚠ 発火＝一発成功率<0.5 or 平均 tool error≥5 で report.py の畳み込みから救出・#76）、書き=evolve batch の増分 ingest（named transcript のみ・max_new cap で runaway 防止・dry-run 安全）。実PJ bench 300件1.12s で impl-worker 0.36 を実測 | `subagent_traces/` + `audit/sections_subagent_traces.py` |
| `verbosity` | 回答冗長性の学習ループ（#75）— standalone（`~/.claude/verbosity/`）を evolve-anything に正式統合。Stop hook `record_verbosity.py`（ゼロLLM・非ブロッキング・足切り800字超の最終 assistant 応答を記録）→ `verbosity/judge.py`（Haiku バッチ判定・dry-run 既定でコスト先出し・llm-batch-guard 準拠・subprocess mock）が「無駄に冗長か」+7パターンを判定し verbose を weak_signals `channel="verbosity"` に emit（reflect 昇格フローに相乗り）。多発パターンから rules/concise.md 追記案を提示（auto-apply しない・protected。output-styles は CC グローバル機能ゆえ自動編集せず）。新ストア `verbosity_candidates.jsonl`（hook writer・ttl45日）+ `verbosity_verdicts.jsonl`（batch writer・permanent）を store_registry active 登録 + keyset snapshot 追従。`audit/sections_verbosity` が冗長率/パターン Top-N を advisory 表示（PJスコープ・重み非反映・floor3件・silence≠evaluated）。judge は絶対 import で `__main__` 直接起動可（audit 案内の `judge.py --run`）。決定論部は LLM 非依存 | `verbosity/` + `hooks/record_verbosity.py` + `audit/sections_verbosity.py` |
| `cross_pj_priority` | confirmed idiom の PJ 横断優先提示 — 他 PJ 承認済みと同テキストの確認 group に `cross_pj_confirmed` ラベル + 先頭表示（提示のみ・自動承認しない、normalize は autopromote と1関数共有）（#462） | `correction_semantic/cross_pj_priority.py` |
| `testpaths_coverage` | pytest 収集漏れの決定論検出 — pytest.ini の testpaths 宣言と実 tests/ ツリーを静的突合し、収集されない tests/ を audit に surface（bare pytest 全件収集の再発防止ゲート）（#468） | `testpaths_coverage.py` |
| `plugin_self` origin | プラグイン本体リポジトリ自身の repo 直下 `skills/` を evolve 診断対象化 — `.claude-plugin/plugin.json` 検出時のみ find_artifacts が追加スキャン、評価は custom 同等・auto-apply は protected（人間承認必須）に降格（#185） | `skill_origin.py` |
| `dogfood gate` | 通し評価ゲート `bin/evolve-dogfood-gate` — Layer1: dry-run SHA256 不変（隔離コピー方式 + 文書化された三層除外）+ 非 dry-run store 差分（Layer1b・`evolve --drain --result-json` の隔離コピー実行で weak_signals 永続化方向を assert）+ 実PJ ingest E2E / Layer2: report invariants / Layer3: SKILL.md コードブロック抽出実行。evolve/（パッケージ）module-level DATA_DIR を env 優先解決に統一（#517）。`--layer light`（Layer1a 不変 + Layer2 + Layer3、約十数秒・Layer1b drain と ingest 除外）を pre-push hook（`scripts/git-hooks/pre-push.local`・非ブロッキング警告）が自動実行。リリース前の実環境 dogfood E2E（#496, #513, #517, #518） | `scripts/lib/dogfood/`, `scripts/git-hooks/` |
| `evolve-release-sync` | リリース後のローカルプラグイン自動同期 `bin/evolve-release-sync` — marketplace は Directory source（ローカル作業ディレクトリ）を見るため、リリースが origin/main に入ってもローカル main を pull しないと `claude plugin update` が古いバージョンを返す穴を塞ぐ。`tag --push` 直後に「ローカル main ff → marketplace update → plugin update」を一括実行。worktree から呼んでも git-common-dir で本体 repo を解決、main 以外チェックアウト中は exit 2。`--dry-run` 対応。`commit-version.md` のリリース手順に組込 | `bin/evolve-release-sync` |
| `pj_slug` | PJ slug 導出の単一ソース — `resolve_pj_slug`（git-common-dir 親・authoritative）/ `pj_slug_fast`（文字列処理・hooks hot path）/ `pj_id_to_slug`（CC エンコード pj_id → 実 dir basename を fs 貪欲復元。`/`↔`-` 両義性を解き token_usage の末尾 split 化け= `figma-to-code`→`code` / `sys-bots`→`bots` を根治 #68）。read/write 同一関数で worktree slug 食い違いを構造的に防ぐ（#492）。sibling-dir worktree の write 時幻 slug は SessionStart で `pj_slug_cache.json` に authoritative 解決をキャッシュし `pj_slug_fast(data_dir=)` が参照して根治（#29） | `pj_slug.py` + `hooks/restore_state.py` |
| weak_signals drain 永続化 | 決定論3チャネルの永続化を `evolve --drain` の apply 境界に配線（`persist_weak_signals_drain`）— 標準フロー＝dry-run 分析のみで書込経路が構造的に死んでいた #484 の根治。pending marker の dry-run 書込は #402/ADR-041 の意図された設計（#513 で復元） | `weak_signals/batch.py` |
| `idiom_filter` | 過汎用 idiom の FP guard — 3 ゲート（最小長 floor 8 / 日常語 stopword / 文脈固有トークン）を `idiom_eligible` に集約し confirmed→idiom_autopromote の FP 製造を遮断。`confirmable_idiom` を bootstrap/daily group に emit し SKILL.md の AskUserQuestion で idiom 単位拒否を可能化（#527, #527-4） | `correction_semantic/idiom_filter.py` |
| `representative` | correction group の representative 品質改善 — `user_only_text` が assistant 引用ブロックを strip し user 発話のみ抽出、`prev_action_summary` が直前 AI 行動 1 行要約を evidence に添える（#528-3 部分） | `correction_semantic/representative.py` |
| remediation 参照リンク相対化 | separation emit prompt のマシン固有絶対パスを PJ ルート相対化（`reference_link_for_prompt`）+ `references/remediation.md` に emit/ingest 6 関数の実 signature 表（#524） | `remediation/fixers_llm.py` |
| `multiview_eval` | evolve 提案を4視点（再利用可能 / 過学習疑い / 退行リスク / コスト増）で決定論分類し audit/evolve に advisory surface。chaos/outcome_attribution/negative_transfer を join、replay は将来フックのみ（#564, tech-eval SEAGym） | `audit/multiview_eval.py`, `audit/sections_multiview.py` |
| `relevance_gate` | 過去経験（weak_signal/idiom）提案を現在文脈との関連度（jaccard 流儀）でゲートし、無関係を理由付きで `suppressed` 分離。`evolve-reflect --show-weak-signals --context` に配線（#565, tech-eval FinAcumen） | `correction_semantic/relevance_gate.py` |
| `report-feedback` | evolve/audit レポートを LLM メタレビューし evolve-anything 自身への改善 issue を todoroki-godai/evolve-anything に半自動起票するスキル。決定論 `evolve_introspect` が拾えない「読んで気づく」改善（表示・提案の質・バグ・UX）が対象で、その dedup/render 配線を再利用。旧 `feedback` スキルの後継として統合・削除 | `skills/report-feedback/`, 契約 `scripts/lib/tests/test_report_feedback_contract.py` |
| `paired_trajectory` | paired trajectory auditing（観測版）— 同一タスク種別を「スキル使用群 vs 非使用群」に分け既存テレメトリからアウトカム差を決定論対照集計し advisory surface（能動再実行なし）。outcome_attribution/multiview_eval/negative_transfer と相補（#15） | `audit/sections_paired.py` + `audit/usage.py` |
| recall `[[link]]` 1-hop | `evolve-fleet recall` の芋づる想起 — fact 本文の `[[name]]` を `Fact.links` 抽出しキーワードヒット fact の同一PJ内 1-hop 先も加算（dangling 無視・重複排除・スコア外）。ADR-025 決定論検索整合（#11） | `fleet/recall.py` |
| recall validity-aware ranking | grounding metadata（valid_from/superseded_at/decay_days）を `recall._score` が消費し stale/superseded memory を降格（superseded>stale・stack しない）。ハード除外せずフォールバック保持＝RaMem(iii)。memory_temporal の既存 API 再利用・SessionStart は #18 で既配線ゆえ対象外（#74） | `fleet/recall.py` |
| reinforce_memory 配線 | dead-code だった `memory_temporal.reinforce_memory()` を recall ヒット時（CLI opt-in でrecall純粋性維持）と SessionStart MEMORY 注入時（有効 memory のみ・stale skip）に本番配線＝忘却曲線強化を実効化（#18） | `memory_temporal.py` + `fleet/recall.py` + `hooks/instructions_loaded.py` |
| temporal provenance 書込配線 | APEX-MEM の `valid_from`+`source_correction_ids`（memory→correction 因果リンク）write 側休眠配線を活性化 — `write_temporal_metadata` を broker `ingest_memory_results` が importance 採点の前に発火（純加算・stale/superseded 非発火）。項目5: session END record に `correction_count`（#2） | `memory_temporal.py` + `auto_memory_broker.py` + `hooks/session_summary.py` |
| subagents/errors 測定バグ修正 | subagents.jsonl の `agent_type` 空ノイズ（約58%）を writer/reader 二重防御で遮断（#36）+ **ID 形ノイズ（pure hex 等・harness が agent_type に ID を渡す）を `rl_common.is_noise_agent_type` 単一ソースで writer+reader 2箇所同時遮断（#44）** + errors.jsonl の `error_type` 常時 unknown を `error_message` 本文から決定論分類（#37）。senpai 独立検証で「集計を分解せず結論した誤診」由来の実バグとして発見・#44 は実 PJ dogfood で発見 | `hooks/subagent_observe.py` + `fleet/collectors.py` + `fanout_cost.py` + `hooks/stop_failure.py` + `rl_common/detection.py` |
| `memory_capability` | 記憶操作を read/use/write/maintain 観点で advisory 評価する observability section（OPD-Evolver #19）。reason 非永続化のため read/use 統合の3軸（write=記憶量 / maintain=健全度 / use_read=活性）。memory dir 解決は `resolve_cc_memory_dir` 単一ソース（CC パスエンコード・#18 と共有。`resolve_pj_slug` repo-basename slug を使うと名前空間食い違いで常時沈黙する dogfood 発見バグの根治） | `scripts/lib/memory_capability.py` + `audit/sections_memory.py` + `pj_slug.resolve_cc_memory_dir` |
| `skill_vuln_scan` | 取り込みスキルの静的脆弱性スキャン（SkillSpector 型）— `.md`/`.sh`/`.bash` を決定論・LLM 非依存で行スキャンし remote_exec/secret_exfil/destructive/prompt_injection/overbroad_tools を検出し audit observability に surface。combo 必須で FP 較正（`gh api ... | base64 -d` 等は非検出・#13） | `skill_vuln_scan.py` + `audit/sections_skill_vuln.py` |
| `fanout_cost` | fan-out 費用対効果の advisory observability section（#14, arXiv 2606.13003）。cost（fan-out session 率 / 平均 subagent / agent_type 内訳・token は体数 proxy）は非スパースで常時算出、advantage（fan-out 群 vs single 群の一発成功率 delta）は #15 同様スパースゆえ各群 ≥5 の floor ゲート付き。subagents.jsonl(agent_type 空除外 #36) + sessions(union read #469) を `_normalize_pj` で当 PJ スコープ | `scripts/lib/fanout_cost.py` + `audit/sections_fanout.py` |
| `memory_contagion` | 評価源バイアスの記憶伝播を audit advisory で検出（human/machine 評価源の蓄積偏り・保守閾値, #73） | `audit/memory_contagion.py` |
| `fleet_queue` | 学習素材ベースの evolve 待ち列挙 `evolve-fleet queue`（#79 Phase 1a）— material_count = weak 未処理 + 前回 evolve 以降の新規 corr が `--threshold`（既定5・env `EVOLVE_QUEUE_THRESHOLD`）以上の PJ を決定論・ゼロ LLM で列挙。`select_evolve_queue` 純関数。新ストア `evolve-queue-state.jsonl`（per-PJ `last_evolve_at`・store_registry active・`writer_locus="batch"`・store_write barrier 経由・`evolve --drain` apply 境界で書込）が「前回 evolve 以降」を PJ 別に測る（既存グローバル `evolve-state.json` を補完）。corrections の `project_path` は `project_name_from_dir` で weak_signals `pj_slug` と名前空間統一。`--json` は Phase 1b #80 契約。 | `fleet/queue.py` + `fleet/queue_state.py` |
| `daily` | 毎朝の evolve queue 自動実行 + SessionStart 通知（#80 Phase 1b）— launchd で `fleet ingest`→`fleet queue --json` を毎朝1回走らせ `evolve-queue.json`（read 専用派生物・SoR でない・store_registry 非登録）に保存。SessionStart hook が待ち PJ を systemMessage（ADR-038）で通知（stale advisory 付き・空なら無音）。無人は決定論パイプラインまで＝適用は対話で人間承認。`bin/evolve-daily-install`(`--time`/`--uninstall`・冪等) + `bin/evolve-daily-run` | `scripts/lib/daily/` + `bin/evolve-daily-install` + `bin/evolve-daily-run` + `hooks/restore_state.py` |

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

# 学習素材ベースで「今 evolve すべき PJ」を列挙（決定論・ゼロ LLM）
bin/evolve-fleet queue                    # weak 未処理 + 新規 corr >= 閾値（既定5）の PJ をテーブル表示
bin/evolve-fleet queue --json --threshold 3
# 毎朝の evolve queue 自動実行を launchd に登録（#80・既定 09:00 / --time HH:MM / --uninstall）
bin/evolve-daily-install
bin/evolve-daily-install --uninstall

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

リリース前は `bin/evolve-dogfood-gate --layer all` も全緑を確認する（pytest が掬えない実環境の繋ぎ目
— dry-run 不変 / report invariants / SKILL.md コードブロック — を検査する。#496）。フル `all` は
Layer1b の drain が重く約3.5分かかる。日常 push は **`--layer light`**（Layer1a 不変 + Layer2 +
Layer3、約十数秒。重い Layer1b drain と ingest E2E を除外）が `pre-push` hook 経由で**非ブロッキング
警告**として自動実行される。hook ソースは `scripts/git-hooks/pre-push.local`、導入は
`bash scripts/git-hooks/install.sh`（gstack-redact の managed pre-push が chain する `pre-push.local`
へコピー。共有 hooks なので worktree 横断で1回でよい）。

**run_evolve を呼ぶ新規テストを書くときは HOME を隔離すること（#457）。** `run_evolve` は
`project_dir=tmp_path` でも後段フェーズ（utterance ingest / prune global check /
weak_signals / correction_semantic）が `Path.home()/.claude/projects`（実環境 ≈9925 jsonl /
1.9GB）を default 走査するため、未隔離だと 1 件数十秒に膨張する。`skills/evolve/scripts/tests/`
配下は conftest の autouse fixture が自動隔離する。別ディレクトリでは
`from test_home_isolation import isolate_home`（`scripts/lib/`）を import し、autouse fixture で
`isolate_home(monkeypatch, tmp_path)` を呼ぶ。ルート conftest の `CLAUDE_PLUGIN_DATA`(=DATA_DIR)
隔離は `Path.home()` 由来パスには効かない点に注意。

## Specification
- 現在の仕様全体像: [SPEC.md](SPEC.md)
- コンポーネント詳細（設計経緯・issue/ADR 参照の SoT）: [spec/components.md](spec/components.md)
- 用語集（Ubiquitous Language）: [CONTEXT.md](CONTEXT.md) — PJ 固有 jargon を 1 語で decode。鮮度は `scripts/lib/glossary_drift.py` が検出し spec-keeper update が advisory 提示。新概念を入れたら CONTEXT.md に 1 行追記する
- 詳細仕様: [spec/](spec/)
- 設計判断の記録: [docs/decisions/](docs/decisions/)
