# evolve-anything — Ubiquitous Language（用語集）

このプロジェクト固有の jargon を 1 語で decode するための共有言語。
AI も人も、ここの用語を使って会話・命名・記述する（Eric Evans, DDD）。

新しい概念を導入したら **必ずここに 1 行追記する**。腐った用語集は無いより悪い。
鮮度は `scripts/lib/glossary_drift.py`（spec-keeper の update が消費）が検出する。

- **意味** は 1 行で。詳細は SPEC.md / docs/decisions/ に委譲する（重複させない）。
- **初出** は概念が最初に入った issue（`#NNN`）または ADR（`ADR-NNN`）。

| 用語 | 意味 | 初出 |
|------|------|------|
| BES | 進化探索。後ろ向きサブゴール分解(#253)と前向き進化探索(#256)の総称 | #253 |
| MemTrace | episodic 検索エラーを 3 類型に分類し event_id へ帰属する診断 | #254 |
| slop | AI 定型句。日英 10 パターンを決定論 regex で検出 | #255 |
| subgoal fitness | 候補を 5 サブゴールに分解して返す密な中間フィードバック | #253 |
| SIRI | 成功軌跡からスキルを採掘→検証→蒸留する3段階。①採掘=`skill_extractor`（discover で発火し triage に合流）/②検証=chaos fitness/③蒸留=evolve | #291 |
| Observe hooks | LLM コストゼロで使用・エラー・修正を自動記録する hook 群 | ADR-002 |
| 直接パッチ最適化 | 遺伝的アルゴリズムでなく LLM 1 パスでパッチを当てる最適化方式 | ADR-003 |
| coherence | fitness の一種。構造的整合性 4 軸スコア | ADR-004 |
| telemetry | fitness の一種。行動実績テレメトリ 3 軸スコア | ADR-005 |
| constitutional | fitness の一種。原則ベース LLM Judge 評価 | ADR-006 |
| env_score | environment fitness の統合スコア（0.0-1.0）。growth-level の素 | ADR-004 |
| cross-PJ recall | keyword 決定論で全 PJ memory を横断検索（vector 非採用） | ADR-025 |
| pitfall-curate | PJ 非依存の pitfalls.md キュレーション（自己進化専用の manager とは別物） | ADR-026 |
| 正準フォーマット収束 | pitfalls.md を寛容パーサでなく書式収束で扱う方針（無破壊 lint） | ADR-027 |
| observability contract | 必ず surface すべき行を単一ソース `_OBSERVABILITY_BUILDERS` 化し markdown/構造化の両経路が消費する契約 | ADR-028 |
| silence ≠ evaluated | 沈黙だと「評価して該当なし」か「配線漏れ」か区別できない。該当なしでも ✓ を1行残す原則 | ADR-028 |
| Belief Entropy | 生成後の memory 要約がソース corrections を保持(retention)・非接地化(drift)していないか測る決定論ゲート。memory_gating(生成前)の後段 | #285 |
| calibration drift | fitness の score-acceptance 相関が閾値を割った状態。audit で可視化＋trigger で evolve-fitness を proactive 提案（変更は人間承認 MUST） | #286 |
| component transfer | 更新コンポーネント（追加スキル）別に既存スキルの成功率 delta を isolation window で分離し「どの更新が回帰させたか」を帰属する negative transfer の ablation 版 | #288 |
| eval saturation | forward-gen trigger eval が「緑でも頑健でない」飽和兆候（positive 偏重/易しい negative/クエリ過少）を eval 実行なし決定論で測る。TASTE 着想、calibration drift と同帯で surface | #292 |
| self_analysis | evolve の result を読み 3 カテゴリ（提案矛盾/phase 例外/系統的却下）の issue 候補を生成する evolve メタ層の自己解析。`evolve_introspect` が決定論生成、Step 11 が人間承認後 todoroki-godai/evolve-anything へ半自動起票 | #299 |
| SkillPyramid | 同一クラスタの低レベル（小型）スキル群を上位スキルへ束ねる階層統合提案。reorganize が split/merge と別軸（低→上位）で検出し max_skill_count 張り付きを構造的に抑える。`hierarchy_candidates` で surface、適用は人間判断 | #303 |
| hook_drift | 他ツール追従 hook（gstack flow 参照 hook 等）の陳腐化を決定論検出する `scripts/lib/hook_drift.py`。第一フェーズは stale_pin のみ | ADR-036 |
| stale_pin | hook が参照する外部ツールの version pin（flow-chain.json の `gstack_version`）が実環境（.last-setup-version）から乖離した状態。表記ゆれが無く false positive しない drift 種 | ADR-036 |
| ファイルベース2相 | claude -p を Python から追い出す3相分離。Phase A（決定論=リクエスト JSON 生成）→ Phase B（assistant がインライン採点/生成、subscription 課金）→ Phase C（決定論=応答パース・ゲート）。Bash 境界を JSON ファイルで越える | ADR-037 |
| llm_broker | ファイルベース2相の共通基盤 `scripts/lib/llm_broker.py`。build_requests/parse_responses/parse_score/passthrough を提供、IO-free・LLM-free（mock 不要） | ADR-037 |
| 編成ギャップ | エージェント *間* の関係（役割重複＝description の役割語 Jaccard / 孤立＝入次数 0 かつ出次数 0）を決定論検出。agent_quality（単体品質）と別軸。observability builder `agent_team` 経由で evolve のたびに surface、整理は人間判断 | #326 |
| data-dir-unified marker | DATA_DIR 一元化済みを示す `~/.claude/evolve-anything/.data-dir-unified`。存在時 hook 文脈の CLAUDE_PLUGIN_DATA（install レイアウト配下）も正準 dir に redirect され hook/tool 分裂が終息。`evolve-fleet migrate-data` が全 entry マージ成功時に設置 | #364 |
| utterance archive | 全PJ human 発話の恒久 DuckDB ストア `utterances.db`。writer は batch ingest のみ（hot path ゼロ）。物理 PK `(source_path,line_no)` + 論理 UNIQUE `(session_id,timestamp,text_hash)` で resume 複製を弾く。pj_slug は transcript の `cwd` 由来（encoded dir 名のデコードは非可逆なので諦める）。query は pj_slug 必須・source_kind デフォルト `dialogue` のみ | #430 |
| weak signals（弱シグナル） | 暗黙修正シグナルの決定論検出レーン `weak_signals.jsonl`。4 チャネル（直後手編集 / permission deny / 言い直し / Esc 中断）をゼロ LLM・バッチ側で検出。corrections 本流に直接入れず（ノイジー）昇格は reflect 確認後（`promoted` フラグ）。言い直し閾値は jaccard 0.8（実コーパス dry-run で決定）。FP は「機構生成テンプレ」という除外理由で直交分離 | #432 |
| writer_locus | store_registry のストア宣言フィールド。書き込み主体が `hook`（hooks.json 登録 hook の append）か `batch`（evolve/audit 等の script）か。`batch` は hook-writer 突合に出ないため stale 突合の対象外（db kind と同じ扱い） | #432 |
| correction capture 二層化 | hot hook（語彙・ゼロ LLM・低レイテンシ）の上にバッチ LLM 意味判定（Haiku・auto_memory 2 相と同型）を足す設計。語彙で拾えない文中・後置・観察型の修正を意味論で拾い weak_signals(channel=llm_judge) へ隔離記録 | #431 |
| 個人辞書 | `correction_idioms.jsonl`。バッチ LLM 判定が抽出した修正言い回し（idiom）を provenance 付きで蓄積。実コーパスで precision 検証後に hot hook の補助パターンへ昇格可能 | #431 |
| human-source（provenance 重み付け） | corrections のうちフェーズ昇格カウントを駆動する出所。`source=reflect_confirmed` のみが human。`source=hook/backfill` や `correction_type=stop`（Stop hook）は機械として除外。機械ノイズで growth フェーズが動かないようにする gate（`provenance_weight`） | #431 |
| llm_judge（channel） | weak_signals レーンのチャネル名。#431 のバッチ LLM 意味判定が検出した修正をこの channel で隔離記録（#432 の決定論 4 チャネルと同じレーンを共有） | #431 |
| bootstrap backlog | 初回 evolve で既存 weak_signals バックログの消化方式を人間が3択（まとめて確認/日次5件/TTL 失効に任せる）で選ぶ phase。marker `bootstrap_done-<slug>.marker` で1回きり | #443 |
| 今日の修正確認（daily review） | evolve の決定論 phase。新規 weak_signal を idiom 単位 group 化し最大5件を y/n 確認 → promote 成功後のみ既読追記（`correction_review_seen.jsonl`）。reflect Step 7.7 の移植 | #446 |
| idiom_autopromote（自動昇格） | confirmed idiom と同テキスト（pj_slug × idiom テキスト単位で照合）の再発 weak_signal を人間確認なしで corrections へ機械昇格。`source=idiom_dict` は HUMAN_SOURCES（根拠は人間の confirm）。安全弁: daily_cap / observability 常時 surface / revoke | #447, ADR-047 |
| revoke（自動昇格の巻き戻し） | `evolve-reflect --revoke-idiom <idiom_key>`。idiom を confirmed=False に戻し（同テキスト全 record）、由来 corrections を `invalidated=True` に原子的 rewrite。invalidated は count_human_corrections から除外＝フェーズ進捗が巻き戻る | #447 |
| measurement_bug（同値一致検査） | 複数 PJ（≥3）で非自明な集計値（0/None 除外）が bit-exact 一致したら測定バグ候補として advisory surface。「全 PJ 同値カウント＝測定バグ強シグナル」の自動化 | #445 |
| growth_report（成長レポート） | evolve レポート末尾の決定論表示「あと N 件で次フェーズ」「今日の昇格成果」。閾値は growth_engine の 6 定数が単一ソース | #448 |
| confirm 配線 | `evolve-reflect --promote-weak` が promote 成功後に対応 idiom を confirmed 化する正準経路。signal→idiom は provenance 物理キー（pj_slug, source_path, line_no）で突合 | #463 |
| 過汎用 idiom FP guard（idiom_eligible） | confirmed→idiom_autopromote の FP 製造を遮断する 3 ゲート（最小長 floor 8 / 日常語 stopword / 文脈固有トークン）を 1 関数に集約。「いやいや」「気がする」等の極短・相槌・日付断片を弾く | #527 |
| confirmable_idiom | bootstrap/daily の確認 group に emit される「はい確定で confirmed=standing auto-promote rule になる idiom テキスト」。eligible 時のみ非 None（過汎用は None＝今回限りの昇格）。AskUserQuestion の判断材料 | #527-4 |
| 重み昇格レディネス（promotion readiness） | outcome 3軸を fitness 重みへ繰り入れてよいかの4条件決定論判定（分散 / 件数下限 / 方向妥当性 / 予測妥当性）。全 ✓ で「重み昇格を提案」を advisory surface | #461, ADR-046, #42 |
| 予測妥当性（predictive validity・順位相関） | 集計平均ベースの skill 順位が分布外（未知セッション=配備時）でも当たるかを in/out-of-sample 分割の Spearman 順位相関で測る、重み昇格レディネスの第4条件。低相関＝過去への過学習＝誤昇格リスクとみなし保守的にブロック。データ不足は「データ不足」と明示し捏造しない | #42, arXiv 2606.19704 |
| cross_pj_confirmed | 他 PJ で confirm 済みの同テキスト idiom を持つ確認 group に付くラベル（slug 一覧）。先頭提示の判断材料であり自動承認はしない | #462 |
| union read | DuckDB ストア（read_only）+ 未 ingest live jsonl を dedup 合算して読む読み取り経路。ingest 後の rotate で jsonl が空でも分母が取れる（sessions: `session_store.read_session_records`、dedup キーは ingest の UNIQUE と同一） | #415 |
| plugin_self | スキル origin の一種 — `.claude-plugin/plugin.json` を持つリポジトリ自身の repo 直下 `skills/` のスキル。evolve は custom 同等に診断するが auto-apply は protected（人間承認必須）。インストール済み他プラグイン（plugin）とは別物 | #185 |
| dogfood gate（通し評価ゲート） | リリース前に実環境の繋ぎ目を3層で検査する `bin/evolve-dogfood-gate`（Layer1: dry-run 不変・隔離コピー / Layer2: report invariants / Layer3: SKILL.md コードブロック実行）。pytest が掬えない「テスト緑・実環境赤」を防ぐ | #496 |
| 隔離コピー方式 | gate Layer1 が実 DATA_DIR を tmp にコピーし `CLAUDE_PLUGIN_DATA` で隔離実行してコピー側のみ比較する方式。ライブ hook の ambient write 偽赤を構造的に排除 | #496, PR #515 |
| 文書化された除外リスト | dry-run 純度契約（1バイトも書かない）の原則ベース例外 — 意図された dry-run 書込（cache warm / 運用ポインタ evolve_pending/）を理由コメント付き定数で除外。bypass フラグは作らない | #513, #496 |
| RODS | reward 分散ベースの進化ターゲット選定（Reward-variance Outcome-Driven Selection）。session 別 reward proxy（一発成功 0/1）の分散が大きい＝能力境界＝学習余地大、とみなし `check_variance` を per-skill 転用して outcome ランキングに advisory 列を添える（自動昇格はしない） | #28 |
| reward EMA（MAA） | バッチ跨ぎ符号付き advantage の指数移動平均（Marginal Advantage Accumulation）。各スキルの baseline 比 一発成功率差を evolve サイクル跨ぎで符号付き EMA 累積し「通時で安定して効くか」を判定。RODS（単一スナップショット分散＝今どこが学習余地か）と相補で、reward EMA＝その余地は本物か（通時で安定して効くか）。書込は `evolve --drain` の apply 境界のみ・閾値は捏造せず符号＋サイクル数のみ（plant-the-seed・3-4 サイクルから有意） | #64, arXiv 2606.20475 |
| subagent_traces | subagent の内部軌跡ストア。transcript の tool_use/tool_result/is_error をパースし「内部で何回 error してからやり直したか」を per-agent_type で測る。親セッションの error_count しか見ない既存 outcome 帰属の盲点（内部 error 連発でも最終成功なら一発成功と誤記録）を塞ぐ advisory。書込は evolve batch の増分 ingest（named transcript のみ・store_write barrier・pj_slug スコープ） | #38 |
| subagent_noise | subagents.jsonl の agent_type ノイズ（本物の Task subagent でない行）の内訳を当 PJ スコープで advisory 分解表示。空文字（compaction/Stop 等で空）と ID 形（harness が hex ID を渡す）に分け件数・率・最古/最新 timestamp を surface。判定は noise_agent_type_kind（is_noise_agent_type と同基準の種別付き単一ソース）。最新が直近7日内なら ⚠（live writer 疑い）、古ければ ℹ（residue・現行 writer は #36/#44 で guard 済ゆえ表示のみ）。reader は既に除外済みで集計非関与 | #142 |
| verbosity（回答冗長性ループ） | AI 応答が「無駄に冗長か」を学習するループ。Stop hook がゼロ LLM で足切り800字超の長応答を candidate 記録 → judge が後段 Haiku バッチで「無駄に冗長か」+7パターン判定 → verbose を weak_signals channel=verbosity に emit（reflect 昇格に相乗り）→ audit が冗長率/パターンを advisory 表示。長さ自体は減点せず水増し・繰り返し・前置き等の無駄を測る。多発パターンは rules/concise.md 追記案を提示のみ（auto-apply しない・output-styles は CC グローバルゆえ自動編集せず）。ユーザー standalone の正式統合 | #75 |
| paired trajectory（観測版） | 同一タスク種別を「あるスキル使用群 vs 非使用群」に分け、既存テレメトリからアウトカム差を決定論で対照観測するシグナル。能動再実行はしない（観測版）。outcome_attribution/multiview_eval/negative_transfer と相補 | #15 |
| write barrier | 全ストア書込の単一ゲート `rl_common.store_write(store_name, record)`。canonical `DATA_DIR/<name>` を内部解決し（呼び出し側は保存先指定不可）、store_registry の active 登録を runtime guard（既定 reject・未登録/非active は `StoreWriteError`）で照合。read（union 寛容）と write（canonical 厳格）を分離し共有は store_registry のみ。緊急避難は env `EVOLVE_WRITE_GUARD=warn` | ADR-049, #55 |
| store_write_raw | write barrier の明示パス例外口（別名関数・フラグでない）。store_registry 照合を通さず指定パスへ直接 append する。raw を使う diff が静的 advisory に必ず上がるよう、`allow_unregistered=True` 的フラグでなく別名にしてある（ADR-049 決定5） | ADR-049, #55 |
| fleet queue（evolve 待ち列挙） | 全 PJ から「今 evolve すべき PJ」を決定論・ゼロ LLM で列挙する `evolve-fleet queue`。学習素材＝material_count = weak 未処理 + 前回 evolve 以降の新規 corr が閾値（既定5）以上の PJ。補助シグナル activity_since はフィルタに使わず理由併記。daily-evolve Epic #78 Phase 1a の入口、`--json` は Phase 1b #80 契約。グローバル correction trigger（即時 reactive）と補完（全 PJ 横断・proactive） | #79 |
| evolve-queue-state | per-PJ の最終 evolve 時刻 `last_evolve_at` を保持する新ストア `evolve-queue-state.jsonl`（append-only `{pj_slug, last_evolve_at, ts}` + read 側 last-append-wins fold）。fleet queue が「前回 evolve 以降」を PJ 別に測るための state。既存グローバル `evolve-state.json` は PJ 別に測れないため新設。store_registry active・writer_locus=batch・store_write barrier 経由・`evolve --drain` apply 境界で書込（dry-run ゼロ書込） | #79 |
