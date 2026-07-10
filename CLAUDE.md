# evolve-anything Plugin

スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化**、**fleet 観測・介入** を提供する Claude Code Plugin。

## 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン |
| フィードバック | reflect, report-feedback | reflect=修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映。report-feedback=evolve/audit レポートを LLM メタレビュー → evolve-anything 自身への改善 issue を todoroki-godai/evolve-anything に半自動起票（決定論 evolve_introspect が拾えない「読んで気づく」改善が対象。旧 feedback スキルの後継） |
| 直接パッチ最適化 | optimize, evolve-loop, generate-fitness, evolve-fitness | corrections/context → LLM 1パスパッチ → regression gate（`scripts/lib/regression_gate.py` に共通化）+ GEPA 数値ガードレール（入力/パッチ char 上限・実データ dry-run 較正・#120） |
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
| Observe hooks (24個 registered) | LLM コストゼロで使用・エラー・修正・ワークフロー・ファイル変更を自動記録 | `hooks/` |
| Auto Trigger | corrections 蓄積・セッション終了等で evolve/audit を自動提案 | `trigger_engine.py` |
| `userConfig` | trigger 閾値・各種上限など 20 項目をプラグイン有効化時に設定可能 | manifest |
| `genetic-prompt-optimizer` | corrections/context ベースの LLM 1パス直接パッチ + GEPA 数値ガードレール（入力件数/pitfalls char 上限・パッチ `char_limit` gate・実データ dry-run 較正 #120） | agent |
| `evolve-loop-orchestrator` | ベースライン→バリエーション→評価→人間確認のループ統合 | agent |
| `evolve-scorer` | オーケストレーター + 3並列サブエージェントで3軸採点 | agent |
| `skill-triage` | CREATE/UPDATE/SPLIT/MERGE/OK の5択判定 | `skill_triage.py` |
| `trigger-eval-generator` | sessions+usage → skill-creator 互換 evals.json 自動生成 | `trigger_eval_generator.py` |
| `evolve-skill` | 自己進化パターン（Pre-flight / pitfalls.md）のピンポイント組み込み | skill |
| `agent-brushup` | エージェント定義の品質診断・改善提案・upstream 監視・model exact-ID pin 検出（#449）+ addyosmani skill anatomy 欠落節の根拠付き改善提案（#63）+ tools 宣言と実付与の乖離検出（`memory:` の Write/Edit 自動付与・#130）+ worker agent の ask-before-fallback 明文化検査（#192） | `agent_quality.py` |
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
| advisory section 共通枠 | 全 observability section の header/trailer 規約を単一化する2層 helper（`advisory_header`/`finalize` + `build_advisory_section`）+ `_OBSERVABILITY_BUILDERS` 横断の契約テストで silence≠evaluated を構造担保。builder 20個が経由、CUSTOM 8個は据え置き（#115） | `audit/advisory.py` |
| `evolve_introspect` | evolve result の自己解析→issue 候補生成（3カテゴリ）[ADR-033, ADR-034] | `evolve_introspect/`（#122 で detectors/render/dedup/helpers に分割・re-export） |
| `evolve_result_schema` | result JSON の正準スキーマ契約 — impl/doc 両 drift 検出（#375, #379） | `evolve_result_schema.py` |
| `evolve_consistency` | P1 invariant の runtime self-detect（型 drift のみ）（#377-5） | `evolve_consistency.py` |
| `hook_drift` | 他ツール追従 hook の陳腐化検出（stale_pin + dead_ref: flow-chain 参照スキルの実在突合、正規化 FP guard 付き #316）[ADR-036] | `hook_drift.py` |
| `data_dir_migration` | DATA_DIR hook/tool 分裂の一元化 migration（marker ゲート redirect + DuckDB rebuild マージ、`evolve-fleet migrate-data`）。session_store も call-time `resolve_data_dir` へ統一し hook/tool split-brain を根治 + marker 済みでも `needs_migration` 再評価で再分裂を再警告（#137）[#364, ADR-042] | `data_dir_migration.py` |
| `spec_trigger` | 仕様未更新マージの SessionStart 検出→spec-keeper 提案 [ADR-044] | `spec_trigger.py` |
| `capture_rate` | correction capture 率（20+ ターン session のうち correction 検出割合）を決定論算出し audit に advisory surface（#421） | `capture_rate.py` |
| `orphan_store` | writer あり reader なしの jsonl ストアを決定論検出（hooks=writer / scripts+skills=reader 静的突合）（#422） | `orphan_store.py` |
| `store_registry` | ストア新設の事前契約ゲート — writer/reader/retention 宣言の機械可読 SoT（jsonl/db 両対応、writer_locus で batch 書込を stale 突合から除外。`status` active/legacy/dead で write 許可を制御 #55）（#430-#434, #55） | `store_registry.py` |
| `store_write` write barrier | 全ストア書込の単一ゲート — canonical `DATA_DIR/<name>` を内部解決し store_registry の active 登録を runtime guard で照合、登録外は既定 reject（`StoreWriteError`／env `EVOLVE_WRITE_GUARD=warn` で緊急避難／registry 不在は fail-open）。例外口は別名 `store_write_raw`。read（union 寛容）と write（厳格）を分離 [ADR-049, #55] | `rl_common/store_write.py` |
| `outcome_metrics` | 行動アウトカム3軸（correction 再発率 / 一発成功率 / rework 率近似）を advisory 表示。一発成功率は `fold_session_error_counts` で distinct session 単位に畳み込み（max 合成）、行数分母と error_count 欠損行の非対称希薄化を根治（#138）。utilization の plugin レイアウト探索修理も同梱 [#423, ADR-046, #138] | `audit/outcome_metrics.py` |
| `utterance_archive` | 全PJ human 発話の恒久アーカイブ utterances.db（extractor/store/ingest/query）。物理PK+論理UNIQUEで resume 複製を弾く・cwd 由来 pj_slug・evolve/audit batch + evolve-fleet ingest + SessionStart staleness advisory（#430） | `utterance_archive/` |
| `outcome_attribution` | outcome 3軸（一発成功率 / rework 率 / correction 再発率）を per-skill 帰属し evolve ターゲットランキングへ自動入力 + negative_transfer gate で退行スキルを末尾 rollback（#10）+ RODS reward 分散列（#28）+ reward EMA 列（バッチ跨ぎ符号付き advantage・#64）。dry-run に before/after 順位差分を surface [#433, #10, #28, #64] | `audit/outcome_attribution.py` |
| `weak_signals` | 暗黙修正シグナルの決定論検出（直後手編集 / permission deny / 言い直し jaccard 0.8 / Esc 中断）→ weak_signals.jsonl レーン。corrections 直入れせず reflect 確認後に昇格。45日 TTL は read 時 age 導出（`is_effectively_expired`）で writer-death 非依存化（#89）（#442, #444, #432） | `weak_signals/` |
| `correction_semantic` | correction capture の二層化（#431）。utterances.db の dialogue 発話を Haiku がバッチ意味判定（auto_memory 2 相と同型）→ weak_signals(channel=llm_judge) 隔離 + 個人辞書（correction_idioms.jsonl）。フェーズ昇格は human-source のみ駆動（provenance_weight）/ reflect 昇格フロー（--show-weak-signals / --promote-weak）[#431] | `correction_semantic/` |
| `bootstrap_backlog` | 初回 evolve で既存 weak_signals バックログの消化方式を AskUserQuestion 3 択で選ぶ bootstrap phase（marker で1回きり・slug スコープ厳守・常時 emit）（#443）+ 対象チャネルを content-rich（llm_judge/rephrase/permission_deny）へ拡張（#99） | `correction_semantic/bootstrap_backlog.py` |
| `daily_review` | evolve の「今日の修正確認」phase — 新規 weak_signal を idiom 単位 group 化し最大5件を y/n 確認、promote 成功後のみ既読追記（既読ストア correction_review_seen.jsonl）（#446）。対象チャネルを content-rich（llm_judge/rephrase/permission_deny）へ拡張し決定論チャネルも evolve 一発で昇格可能化、content-poor（esc/手編集）は detector 文脈未保存ゆえ除外し observability 集計に残す（#99） | `correction_semantic/daily_review.py` |
| `review_channels` | y/n 確認に出す weak チャネルの単一ソース（#99）— `REVIEW_CHANNELS`（content-rich）+ channel 別代表テキスト `signal_text` + `grouping_keywords`（`_strip_path_words` で path 語を除外し over-merge 解消）を bootstrap_backlog/daily_review/promote が共有 | `correction_semantic/review_channels.py` |
| `idiom_autopromote` | confirmed idiom と同テキストの再発 weak_signal を機械昇格（照合は pj_slug × idiom テキスト単位）。安全弁3つ: daily_cap / observability 常時 surface / `evolve-reflect --revoke-idiom` 巻き戻し（#447, ADR-047）。confirmed 化の正準経路は `evolve-reflect --promote-weak` の confirm 配線（#463） | `correction_semantic/idiom_autopromote.py` |
| `measurement_bug` | 複数 PJ で非自明な集計値が bit-exact 一致したら測定バグ候補として advisory surface（≥3 PJ・0/None は構造的に除外）（#445, #185） | `audit/measurement_bug.py` |
| `growth_report` | evolve レポート末尾に成長状態を決定論表示 — あと N 件で次フェーズ / 今日の昇格成果。閾値は growth_engine の定数が単一ソース（#448） | `growth_report.py` |
| `outcome_promotion_readiness` | ADR-046 重み昇格レディネスの4条件決定論判定（分散 / 件数下限 / 方向妥当性 / 予測妥当性#42）— ✓✗ + evidence で advisory surface、全 ✓ で「重み昇格を提案」。session 系分母は session_store union read（db read_only + 未 ingest jsonl）で実効化済み。条件3 は同一 apply の二重 anchor を `(pj,axis,before,after)` dedup で非独立証拠の二重計上から救出（#77）。`per_pj_first_try_success` の分母も `fold_session_error_counts` 共有で distinct session 化（#138）（#461, #469, #42, #77, #138） | `audit/outcome_promotion_readiness.py` |
| `predictive_validity` | 重み昇格レディネスの第4条件（#42）— skill_activations×sessions の per-skill 一発成功率を ts 中央値で in/out-of-sample 分割し、共通出現 skill（≥5）の順位を純Python Spearman で相関。rho≥0.5 で pass、insufficient_data は「データ不足」明示で捏造せず保守的に昇格ブロック（誤昇格抑制）。集計平均順位の分布外転移を検出（arXiv 2606.19704） | `audit/predictive_validity.py` |
| `reward_ema` | バッチ跨ぎ符号付き advantage の EMA 累積（MAA・α=0.3）で「通時で安定して効くか」を RODS（単一スナップショット分散・#28）と相補判定。新ストア `reward_ema.jsonl`（active・batch writer）。読み=phases_diagnose の advisory 列（順位非影響）・書き=`evolve --drain`（#64, arXiv 2606.20475） | `audit/reward_ema.py` |
| `subagent_traces` | subagent 内部軌跡ストア（#38）— transcript の内部 tool error/やり直しを per-agent_type で advisory 表示し親 error_count のみの outcome 帰属盲点を塞ぐ。ストア `subagent_traces.jsonl`（active・batch）。⚠＝一発成功率<0.5 or 平均 tool error≥5（#76）。増分 ingest は `evolve --drain` 境界（#135）。`write_trace(data_dir=)` で read/write 隔離を対称化（#140） | `subagent_traces/` + `audit/sections_subagent_traces.py` |
| `subagent_noise` | subagents.jsonl の agent_type ノイズ内訳を advisory 分解表示（#142-8b）— 当PJスコープで空文字/ID形に分け件数・率・最古/最新 timestamp を surface。判定は `noise_agent_type_kind`（`is_noise_agent_type` と同基準の種別付き単一ソース）。最新ノイズが直近7日内なら ⚠（live writer 疑い）、古ければ ℹ（residue・現行 writer は #36/#44 で guard 済ゆえ表示のみ・reader は既に除外・スコア非関与）。ノイズ0は None（無ければ非表示） | `audit/sections_subagent_noise.py` + `rl_common/detection.py` |
| `verbosity` | 回答冗長性の学習ループ（#75）— Stop hook `record_verbosity.py`（ゼロLLM）→ `verbosity/judge.py`（Haiku バッチ判定）が冗長を weak_signals `channel=verbosity` に emit（reflect 昇格に相乗り）。多発パターンから rules/concise.md 追記案を提示（auto-apply しない）。`audit/sections_verbosity` が冗長率を advisory 表示 | `verbosity/` + `hooks/record_verbosity.py` + `audit/sections_verbosity.py` |
| `cross_pj_priority` | confirmed idiom の PJ 横断優先提示 — 他 PJ 承認済みと同テキストの確認 group に `cross_pj_confirmed` ラベル + 先頭表示（提示のみ・自動承認しない、normalize は autopromote と1関数共有）（#462） | `correction_semantic/cross_pj_priority.py` |
| `testpaths_coverage` | pytest 収集漏れの決定論検出 — pytest.ini の testpaths 宣言と実 tests/ ツリーを静的突合し、収集されない tests/ を audit に surface（bare pytest 全件収集の再発防止ゲート）（#468） | `testpaths_coverage.py` |
| `plugin_self` origin | プラグイン本体リポジトリ自身の repo 直下 `skills/` を evolve 診断対象化 — `.claude-plugin/plugin.json` 検出時のみ find_artifacts が追加スキャン、評価は custom 同等・auto-apply は protected（人間承認必須）に降格（#185） | `skill_origin.py` |
| `scaffold_advisory` | advisory 3点セット追加の scaffold — #115 共通枠を使った observability builder stub のテンプレ生成 + 多点配線チェックリストで keyset snapshot 追従漏れ等のミス面を下げる。`bin/evolve-scaffold-advisory <name> [--with-store] [--write]`（既定 dry-run）。(c) 低トラフィック畳み込みは既存 fold_clean_observability で対応済（#118 (b)） | `scaffold_advisory.py` |
| `dogfood gate` | 通し評価ゲート `bin/evolve-dogfood-gate` — Layer1: dry-run SHA256 不変 + 非 dry-run store 差分 + 実PJ ingest E2E / Layer2: report invariants / Layer3: SKILL.md コードブロック実行。`--layer light` を pre-push hook が非ブロッキング警告で自動実行（#496, #513, #517, #518） | `scripts/lib/dogfood/`, `scripts/git-hooks/` |
| `evolve-release-sync` | リリース後のローカルプラグイン自動同期 `bin/evolve-release-sync` — marketplace は Directory source（ローカル作業ディレクトリ）を見るため、リリースが origin/main に入ってもローカル main を pull しないと `claude plugin update` が古いバージョンを返す穴を塞ぐ。`tag --push` 直後に「ローカル main ff → marketplace update → plugin update」を一括実行。worktree から呼んでも git-common-dir で本体 repo を解決、main 以外チェックアウト中は exit 2。`--dry-run` 対応。`commit-version.md` のリリース手順に組込 | `bin/evolve-release-sync` |
| `pj_slug` | PJ slug 導出の単一ソース — `resolve_pj_slug`（git-common-dir 親・authoritative）/ `pj_slug_fast`（hooks hot path）/ `pj_id_to_slug`（CC pj_id → 実 dir 貪欲復元・#68）。read/write 同一関数で worktree slug 食い違いを防ぎ、sibling worktree の幻 slug は SessionStart cache で根治（#492, #29） | `pj_slug.py` + `hooks/restore_state.py` |
| weak_signals drain 永続化 | 決定論3チャネルの永続化を `evolve --drain` の apply 境界に配線（`persist_weak_signals_drain`）— 標準フロー＝dry-run 分析のみで書込経路が構造的に死んでいた #484 の根治。pending marker の dry-run 書込は #402/ADR-041 の意図された設計（#513 で復元） | `weak_signals/batch.py` |
| reconcile_surfaced drain 永続化 | remediation 連続提示 count marker（`remediation_surfaced/<slug>.json`）の書込＋閾値到達 record_rejection を `evolve --drain --result-json` の apply 境界へ移設（#186）— phases は dry-run で `persist=False` の表示用判定のみ。標準フロー＝dry-run のみで marker が永久未書込＝閾値未達で #494 の自動却下が全 PJ 死蔵していた根治。`_tracked` 再構築は `build_reconcile_tracked` で phases/drain 単一ソース化（weak_signals #484 と同型） | `cli.py` + `_env.py` + `phases_remediate.py` |
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
| `skill_vuln_scan` | 取り込みスキルの静的脆弱性スキャン（SkillSpector 型）— `.md`/`.sh`/`.bash` を決定論・LLM 非依存で行スキャンし remote_exec/secret_exfil/destructive/prompt_injection/overbroad_tools を検出し audit observability に surface。combo 必須で FP 較正（`gh api ... | base64 -d` 等は非検出・#13）+ 同一スコープ内のマルチステップ攻撃系列（fetch→変数/ファイル経由 exec）の静的フロー検出（#123） | `skill_vuln_scan.py` + `audit/sections_skill_vuln.py` |
| `fanout_cost` | fan-out 費用対効果の advisory observability section（#14, arXiv 2606.13003）。cost（fan-out session 率 / 平均 subagent / agent_type 内訳・token は体数 proxy）は非スパースで常時算出、advantage（fan-out 群 vs single 群の一発成功率 delta）は #15 同様スパースゆえ各群 ≥5 の floor ゲート付き。subagents.jsonl(agent_type 空除外 #36) + sessions(union read #469) を `_normalize_pj` で当 PJ スコープ | `scripts/lib/fanout_cost.py` + `audit/sections_fanout.py` |
| `memory_contagion` | 評価源バイアスの記憶伝播を audit advisory で検出（human/machine 評価源の蓄積偏り・保守閾値, #73） | `audit/memory_contagion.py` |
| `memory_guard` | auto-memory 書込境界の runtime 記憶汚染検出（#108）— skill_vuln_scan の較正済みパターンを再利用し prompt_injection / secret_exfil の2カテゴリのみ reject（誤 reject コスト大ゆえ限定）。broker の `ingest_memory_results` が write 前に検査（fail-open・contaminated は consumed で無限リトライなし・`EVOLVE_MEMORY_GUARD=warn` で降格）。audit に汚染 advisory surface | `memory_guard.py` + `auto_memory_broker.py` + `audit/sections_memory.py` |
| `fleet_queue` | 学習素材ベースの evolve 待ち列挙 `evolve-fleet queue`（#79）— material_count = weak 未処理（content-rich channel 限定・#113）+ 前回 evolve 以降の新規 corr が閾値以上（既定5）の PJ を決定論・ゼロ LLM で列挙。新ストア `evolve-queue-state.jsonl`（active・batch writer）。dead/phantom/未帰属 corr を footer で透明化（#85-#96） | `fleet/queue.py` + `fleet/queue_state.py` + `fleet/cli.py` + `fleet/collectors.py` + `fleet/formatters.py` |
| `daily` | 毎朝の evolve queue 自動実行 + SessionStart 通知（#80 Phase 1b）— launchd で `fleet ingest`→`fleet tokens --backfill`（増分・#157）→`fleet queue --json` を毎朝1回走らせ `evolve-queue.json`（read 専用派生物・SoR でない・store_registry 非登録）に保存。SessionStart hook が待ち PJ を systemMessage（ADR-038）で通知（stale advisory 付き・空なら無音）。無人は決定論パイプラインまで＝適用は対話で人間承認。`bin/evolve-daily-install`(`--time`/`--uninstall`・冪等) + `bin/evolve-daily-run` | `scripts/lib/daily/` + `bin/evolve-daily-install` + `bin/evolve-daily-run` + `hooks/restore_state.py` |
| `artifacts_hygiene` | artifact 衛生5検出器（#124 グローバル CLAUDE.md 空/未存在 / #125 SKILL.md 欠落 dir / #126 バックアップ残置 / #129 skill 名跨 scope 重複・symlink wrapper 除外 / #155 plugin と重複するグローバル hook 残骸・同一イベント×正規化 basename 一致）を #115 advisory 共通枠で observability に surface。2026-07-03 PC 環境手動監査の検出器ギャップ起票分（決定論・LLM 非依存） | `audit/sections_artifacts.py` |
| `memory_hygiene` | memory dir 衛生3検出器（#127 MEMORY.md 索引孤児 / #128 auto-memory frontmatter スキーマ検証 / #131 旧 PJ memory 完全重複残骸=fleet 横断・tar 退避手順提案のみ auto-apply なし）。3件とも clean 時非表示・走査は *.md のみで jsonl 非対象 | `memory_index_orphan.py` + `memory_schema_check.py` + `memory_dup_residue.py` + `audit/sections_memory.py` |
| `invalid_frontmatter` | YAML frontmatter が壊れて CC 発火不能なスキルを直接 surface する observability section（#167）。`parse_frontmatter` が YAMLError を握り潰し `{}` 返却する穴を検出コア `detect_frontmatter_error`（純関数・`parse_frontmatter` 無改変）で塞ぎ、effort_detector が invalid を skip（missing_effort 誤分類根治・#166 相互作用）+ advisory section（⚠・clean時沈黙・auto-fix なし人手提案）。scope は `.claude/skills/**/SKILL.md` | `frontmatter.py` + `effort_detector.py` + `audit/sections_invalid_frontmatter.py` |
| `self_contamination` | 自己汚染ハルシネーション指紋（A=生タグ漏出 / B=偽 system-reminder / C-lite=汚染宣言×tool_result 原文非在）を transcript 走査で恒久計測する Layer 2 observability section（ゼロLLM・read-only・hook/store 新設なし）。tool_result 原文と assistant text/thinking を厳密分離し byte 照合、operational/話題PJ 分離集計、clean 時沈黙。live 抑止 Layer 1 hook は要否をこの計測で判断 | `self_contamination_scan.py` + `audit/sections_self_contamination.py` |
| `evolve-tier` | モデルティア（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）の正典を `~/.claude/model-tiers.json` に一元化する CLI（#193）— `set`（正典更新）/`sync [--apply]`（agent frontmatter・settings.json・routing rule マーカーへ反映、既定 dry-run・冪等）/`drift`（stale なモデルエイリアスの散文残存を advisory 検出）。`agent_tier` の gate は call-time でこの config を参照 | `bin/evolve-tier` + `tier_policy.py` + `tier_policy_sync.py` + `tier_policy_drift.py` + `tier_policy_cli.py` |
| `judge_audit` | LLM judge の false-pass 欠陥注入監査（The Blind Curator arXiv 2607.07436・#188）。既知の欠陥 fixture 6件を judge の実プロンプト/パーサ（constitutional 再利用）に流し、失敗を合格と誤判定する割合を opt-in CLI ハーネス（dry-run 既定・llm-batch-guard 準拠）で計測。false-pass はスキル退役（negative_transfer rollback）を無音で無効化するため事前計測する。floor=min(5, fixture総数)・率20%超で ⚠ を advisory surface（verbosity #75 と同型の分離） | `scripts/lib/judge_audit/` + `audit/sections_judge_audit.py` |

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

# advisory 3点セット追加の scaffold（module stub 生成 + 多点配線チェックリスト・#118）
bin/evolve-scaffold-advisory my_check                 # dry-run（stub + checklist 表示）
bin/evolve-scaffold-advisory my_check --with-store --write

# モデルティア正典の一元管理（#193）
bin/evolve-tier show                      # ティア表 + 正典ソース（file/defaults）を表示
bin/evolve-tier set HEAD --model sonnet --effort max   # 正典を更新（atomic write）
bin/evolve-tier sync                      # targets への反映を dry-run（diff 表示のみ）
bin/evolve-tier sync --apply              # drift のみ実書込（冪等）
bin/evolve-tier drift                     # 正典に無いモデルエイリアスの散文残存を検出

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
