# ADR-055: Codex rollout ログの観測取り込み（第3版）

- Status: **Draft（設計レビュー待ち。[Must] 残存中は実装着手しない）**
- Date: 2026-08-23
- Related: ADR-052（Claude primary / Codex opt-in lanes）, #245（codex_usage）, #379（新設凍結）, #28（transcript store 規模破綻）
- 想定 issue: 未起票

### 第2版からの主な変更点（レビュアーはここだけ読めば差分が分かる）

- **裁定A: Phase 1 から D2（extractor リファクタ）を外す**。codex と tacchi の意見が割れた点への頭の裁定。Phase 1 は**既存リポジトリのコードを一切変更しない使い捨てスクリプト1本**に限定し、D2（source 別 parser + 共通 reducer への分離）は **Phase 2 の先頭**へ移す。これにより No-Go 時の後始末が「一時ディレクトリを消すだけ」になり、tacchi Must-5（後始末の自己矛盾）も同時に解消する
- **裁定B: 今回の C-0 較正は No-Go 判定に使わない**。Codex 契約変更（2026-08-23）以前のログが大半を占め、指摘率が低くても「Codex ログから指摘が取れない」証明にならない（tacchi Must-1）。今回は**移行前ベースライン**として記録するのみとし、Go/No-Go の確定は Codex 移行後ログが2〜3週間分貯まってから同じ器で再測して行う。Phase 1 の完了条件を「移行前ベースラインが取れたこと」に変更した
- **X1: dedup key をチャネル制約付きマッチングへ変更**。グローバル閾値方式は偽統合を生む（実測）。「同一ファイル・同一 text_hash・異チャネル間のみ」の 1:1 貪欲マッチング（T=100ms）を採用。最終分母は 259 件（保守値 T=0 で 264 件）
- **X5: セグメント帰属アルゴリズムを訂正（重大な発見）**。「先頭 `session_meta.id` を全体に付与」は user 発話305件の誤帰属・UNIQUE キー衝突78件を起こすことが実測で判明。「各発話をレコード順で直前に出現した `session_meta` セグメントに帰属させる」に変更し、D5 を D5a（Phase 1・識別子とセグメント帰属）と D5b（Phase 2・source の永続化）に分割した
- **Go/No-Go を3値判定に変更**（絶対閾値の恣意性を排除）。分母セマンティクスを CC/Codex 間で「判定結果が確定した発話数」に揃えた（判定失敗・omitted verdict は別表示）
- **M10 を訂正**: `correction_judged.jsonl` の内訳は keyed 5,372 行 / keyless 77 行（コスト予約行）。全体率は 7.32% ではなく **7.43%（399/5,372）**が正しい。**PJ 別の 4.48%（evolve-anything）は keyed のみの集計であり無傷**
- **C-0 の隔離を実コードの DI パラメータに合わせて再設計**。`run_daily_judge` の `judged_path` / `weak_signals_path` / `idioms_path` / `utterances` を一時ディレクトリへ向け、実行前後で本番3ストアの byte hash 不変を検査する契約にした
- **凍結ゲートの検査対象を訂正**: `CULLED_OBSERVABILITY_SECTIONS`（表示抑制集合）ではなく、live `_OBSERVABILITY_BUILDERS` と `FROZEN_OBSERVABILITY_SECTIONS` の**差分**が正しい検査対象
- **M3/tacchi の数値差分（153/675 vs 213/675）は解明済み**（矛盾ではなく母集合の違い）。未解決リストから除外した
- Test Plan の変異表を修正: ①②の独立性を修正、④を鮮度変異へ差し替え、X5 由来の新変異（セグメント帰属の巻き戻し）を追加
- **未決事項を1つの表に集約**し、各項目が Phase 1 着手のブロッカーかを明示した

## Context

### 何が変わったか（前提の変化）

ユーザーが Codex の契約を max プラン相当 ×5 枠へ変更した（2026-08-23）。
これにより **Codex 枠の限界費用がほぼゼロ**になり、実装の主戦場を Codex へ移す方針が決まった
（ユーザー決定・同日）。ADR-052 が定めた「Claude Code = primary executor / Codex = opt-in」
の前提（「常時2agentを動かす費用は不要で、通常実装はトークン余力の大きいClaude Codeを中心に
したい」）は、**費用前提が反転したため失効している**。

### 何が問題か

evolve-anything の柱2（フィードバック）は、Claude Code の会話ログから
「ユーザーがAIを訂正した箇所」を拾って skill/rule に焼き戻す。
実装を Codex に移すと、**その作業の訂正が一切拾えなくなる**。
自分の製品の学習材料を、自分で断つことになる。

現状 `scripts/lib/fleet/codex_usage.py`（#245）は Codex を観測しているが、
読んでいるのは `~/.codex/state_5.sqlite` の `threads`（cwd / tokens_used / updated_at）だけで、
**会話本文は含まれない**（実コードで確認済み）。観測しているのは「量」であって「中身」ではない。

## Evidence（実測値）

design-review-gate.md の入口条件に従い、本設計が依拠する前提に evidence を埋める。
**測定できていないものは「未測定」と明示し、推定値で埋めない。**

### 第1版から確定した Evidence（E1〜E15 相当。値は据え置き、再確認済み）

| # | 前提 | 値 | 取得方法 | 状態 |
|---|---|---|---|---|
| E1 | Codex の会話ログ実体の場所 | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` | `find ~/.codex -name '*.jsonl'` | 実測 2026-08-23 |
| E2 | 規模 | **675 ファイル**（第1版計測時 671 から増加。差は測定間の通常利用。本タスクの再測では **677**） | `find … \| wc -l` | 実測 2026-08-23 |
| E3 | 1行の構造 | トップレベルキーは `timestamp` / `type` / `payload` の3つのみ | 全数走査 | 実測 2026-08-23 |
| E6 | tool 実行が取れるか | ○ `response_item.function_call`(name/arguments/call_id) + `function_call_output` | 同上 | 実測 |
| E9 | モデル名 | ○ `turn_context.model` + `effort` | 同上 | 実測 |
| E10 | 遡り取り込みの可否 | ○ 日付ディレクトリ構造で1ヶ月以上前が残存 | `find` | 実測 |
| E11 | CC 側 ingest 入口 | `scripts/lib/utterance_archive/ingest.py:129` `ingest_all_projects()` | コード確認 | 実測 |
| E12 | CC 側 抽出本体 | `scripts/lib/utterance_archive/extractor.py:244` `extract_utterances()` | コード確認 | 実測 |
| E13 | 指摘判定の所在 | extractor にヒューリスティックは無い。判定は `scripts/lib/correction_semantic/judge_runner.py` が `safe_llm_call` 経由で行う非対話 daily runner（第1版が指した `batch.py` の Phase A/C は決定論 emit/ingest のみで、LLM 判定＝Phase B は judge_runner が担う。両者とも読んだ） | コード確認 | 実測 |
| E14 | `state_5.sqlite` に本文は無いか | 無い（`cwd`/`tokens_used`/`updated_at` のみ） | `codex_usage.py` + read-only 実測 | 実測 |
| E16 | CC 側の現規模（比較対象。tacchi Should6 反映） | **4,196 jsonl / 9.1GB**（第1版時点の #28 記録値 9925 jsonl / 1.9GB から再測値へ差し替え。tacchi 報告値 4,194 とほぼ一致、差2件は測定タイミングの通常利用） | `find ~/.claude/projects -name '*.jsonl' \| wc -l`, `du -sh` | 実測 2026-08-23（本タスクで再実行） |

### 新規実測（M1〜M10、全数走査・2026-08-23。スクリプトと生データは scratchpad の `measure.py`/`measure2.py`/`m6_full.py`/`results.json`/`m5_detail.json`/`m6_full.json`/`child_files.json`/`sub_agent_thread_ids.json` に保存。頭が引き継ぐ場合は要移設）

**M1 role=user の純度（機構マーカー混入率）**

- 母数: `response_item.message(role=user)` 3,582件 / `event_msg.user_message` 2,773件
- response_item 側の先頭タグ内訳: `(no_tag)` 2170 (60.6%) / `recommended_plugins` 584 (16.3%) / `task-notification` 491 (13.7%) / `command-name` 141 / `local-command-stdout` 133 / `command-message` 27 / `skill` 26 / `environment_context` 7 / `user_action` 2 / `image` 1
- 機構混入率 **39.4%**（response_item 側）/ **28.6%**（event_msg 側）
- event_msg 側には `recommended_plugins` / `skill` / `environment_context` / `user_action` / `image` は**出現しない**

**M2 二重表現**

- 同一 `text_hash` が両系統に出現: 2,509件 / response_item のみ 795件 / event_msg のみ 3件
- timestamp 差: 完全一致 2,291 / <10ms 203 / <100ms 15（>=1s は 0件）
- **【第2版の記述を撤回】** 第2版は「dedup key `(file, timestamp, text_hash)` は実測で機能する」としたが、これは timestamp *完全一致*を前提にした記述であり不正確。**X1（下記）で timestamp 完全一致でない218件を発見し、閾値方式（グローバル T）は偽統合を起こすことを実測**した。正しい方式は X1 の「チャネル制約付き 1:1 貪欲マッチング」を参照

**M3 sub-agent の構造（第1版の前提 E18 が覆った箇所）**

- `event_msg.sub_agent_activity` は 154 ファイルに出現。フィールドは `{event_id, type, kind, agent_thread_id, agent_path, occurred_at_ms}` のみで**会話本文を一切含まない**。`kind`: started 186 / interacted 142 / interrupted 5
- `type=inter_agent_communication_metadata` は 199 ファイルに出現。`payload={trigger_turn: bool}` のみのマーカー行
- **決定的事実**: `sub_agent_activity.agent_thread_id` は**別ファイル**の `session_meta.id` と一致する。つまり sub-agent の発話は同一ファイル内に行として混在しない（CC の `isSidechain: true` 行内蔵方式とは構造が異なる）
- 全数機械判定した子セッションファイル: **153/675 = 22.7%**

**【解明済み・第2版の「未解決」を解消】** codex レビューの 213/675（31.6%）・745/6354（11.7%）と本測定の 153/675（22.7%）は矛盾ではない。**母集合が異なる**: codex 側は「`sub_agent_activity`/`inter_agent_communication_metadata` マーカーを*含む*ファイル」＝**親ファイル側**を数えており、本測定は「`agent_thread_id` が他ファイルの `session_meta.id` と一致する」＝**子ファイル自身**を数えている。親が子を起動したマーカーを持つのは当然であり、両者は独立した集合で二重計上ではない。実装着手前の追加調査は不要と判定した。

**M4 session_id とファイルの対応**

- `session_meta` レコード総数 775（**複数 `session_meta` を持つファイルが 100件**、本タスクの再測では101件）
- `id == session_id` 627件 / `id != session_id` **148件（19%）**
- ファイル名 UUID と 先頭 `session_meta` の `id`: **675/675 完全一致**

**【第2版の記述を撤回】** 第2版はこの100件（複数 `session_meta` を持つファイル）について「先頭IDを採用した場合の影響は未測定」としていたが、**X5（下記）で実測した結果、先頭ID一律付与は user 発話305件を誤帰属させ、UNIQUE キー衝突を78件発生させることが判明**した。D5a はこの実測に基づき帰属方式を確定させた。

**M5 CLI version 別 type 分布**

- 出現版: 0.145.0(182) / 0.145.0-alpha.27(50) / 0.0.0(1) / 0.147.0(494) / 0.146.0-alpha.3.1(1) / 0.149.0(47)
- (type, payload.type) 組の集合サイズ: 28 / 7 / 7 / 22 / 13 / 17（**単調増加ではない**）

**【第2版の記述を撤回】** 第2版は「Phase 1 の対象期間に出る version だけ通せばよい」としていたが、**X4（下記）の実測でこの方針は8種の (type, payload.type) 組を静かに取りこぼす**ことが判明した。version フィルタ方式自体を撤回し、パーサは組そのもので判定する方式に変更した（D3・Phase 1 スコープ参照）。

**M6 pj_slug 帰属（新規に発覚した重大問題）**

- cwd 欠落 0件 / ユニーク slug 91 / 上位 evolve-anything 241, figma-to-code 52, ai-daily-report 37, updater-index 35, amamo 27
- **既存 `pj_slug_fast` の worktree 正規化は `/.claude/worktrees/` というマーカー文字列に依存するが、Codex の worktree cwd はこのマーカーを持たない**（マーカーヒットは 33件のみ）。結果 `wt-415` / `wt-369` / `updater-index-wt-trackb` 等の worktree 生 basename がそのまま slug として **195件 = 28.9%** 残り、本体 repo に正規化されない
- 同一 cwd に `~/.claude/projects/<encoded>` が存在するファイル: 402/675 (59.6%)

**M7 UNIQUE 衝突**

- key `(session_meta.id, timestamp, text_hash)` でユニークキー 3,812 / 衝突 0件（**先頭 `session_meta.id` のみを使った測定**）

**【第2版の記述を撤回】** この「衝突0件」は先頭 `session_meta.id` をファイル全体に一律付与した場合の measurement であり、**後続 `session_meta` セグメント由来の発話まで正しく別 id へ帰属させると、X5 実測で 78件の UNIQUE 衝突が発生する**ことが判明した。0件という結果自体は測定条件が限定的だったための見かけであり、D5a の帰属方式変更でこの問題は解消する。

**M8 LLM 呼び出しの集約**

- `subprocess.run(["claude", ...])` の実呼び出しは `scripts/lib/safe_llm_call.py:145-147` の `call_claude_headless` **1箇所のみ**
- 呼び出し元は `scripts/lib/correction_semantic/judge_runner.py:94` と `scripts/lib/verbosity/judge.py:89` の2箇所のみ
- anthropic / openai SDK の直接呼び出しは `scripts/lib/` 配下（tests 除く）に 0件

### 既存スキーマとの不整合（第1版に無かった実測。反映必須4）

- 現行 `Utterance`（`scripts/lib/utterance_archive/extractor.py:63-75`）は
  `source_path / line_no / pj_slug / session_id / timestamp / text / text_hash / prev_action / source_kind / extractor_version` の10フィールドのみ
- 現行 `utterances` テーブル DDL（`scripts/lib/utterance_archive/store.py:32-45`）も同フィールド + `ingested_at`。**`role` / `tool_names` / `source` / `model` の列は存在しない**。**注意（codex Should反映）**: `CREATE TABLE IF NOT EXISTS` は既存テーブルに列を増やさないため、将来 (a) 列追加案を採る場合は ALTER/migration と `_COLUMNS` 更新が別途必須（`store.py:31-47`）。これを Phase 2 の入口条件とする
- 物理 PK は `(source_path, line_no)`、論理 UNIQUE は `(session_id, timestamp, text_hash)`（`store.py:44,46-47`）

### 未測定（第3版でも残る項目）

- **E17 取り込み後の wall time / DB 増分**: Test Plan C-1 で必須計測とし、設計確定の対象外とする（実装時に測る性質の値のため）
- **X7 子セッションの user 発話が委譲プロンプトか人間介入か**: **測定中・結果待ち**。Phase 1 の完了条件には含めない（D4 の除外判定は構造的事実だけで確定しており、X7 の結果に依存しない）

## X1〜X6 実測（レビュー指摘の裏取り・全数走査・2026-08-23）

スクリプトは scratchpad の `measure3.py` / `measure4.py`、結果は `m3_*.json` / `m4_*.json`。ファイル数は前回675 → **677**（2件増）。

**X1 dedup key の取りこぼし**

- 両系統マッチ済み 2,509件中、timestamp 完全一致でない **218件**。delta 分布: 1ms=170 / 2ms=13 / 3ms=4 / 4ms=7 / 5ms=1 / 6ms=4 / 7ms=2 / 8ms=2 / 11〜66ms に13件散在。**最大 66ms**
- グローバル閾値方式（チャネル区別なし）の副作用: T=1ms で 正しく統合170/偽統合30、T=10ms で 203/143、T=100ms で 218/355、T=1000ms で 218/495
- **採用**: グローバル閾値は不採用。**「同一ファイル・同一 text_hash・かつ response_item ↔ event_msg の異チャネル間のみ」に限定した 1:1 貪欲マッチング、T=100ms**（観測最大 66ms に余裕）。同一チャネル内を比較しないため**偽統合が構造的に発生しない**。実測: T=0 で最終264件 / T=66〜100ms で259件（5件が追加統合、偽統合0）

**X2 子セッション参照集合の走査範囲**

- Phase 1 対象ファイル: **227件**
- 参照集合サイズ: 全677走査→153件 / Phase1限定走査→90件
- Phase 1 対象のうち子と判定される件数: 方式A（全ファイル参照）=90件 / 方式B（Phase1限定参照）=90件 → **差分0件**
- 境界跨ぎ（親が Phase1 外・子が Phase1 内）: **0件**。逆方向も **0件**
- **採用**: 方式B（Phase 1 限定走査）。全677走査が不要でコスト減、結果は同一。**ただし「0件は実測時点の事実であり、将来別期間で境界跨ぎが起きない保証ではない」ことを Consequences に明記する**

**X3「直近14日」の定義**

- 基準日 2026-08-23。3方式（日付ディレクトリ / レコード timestamp / ファイル mtime）すべて **443/677件で完全一致・差分0・unknown 0**
- **採用**: **日付ディレクトリ方式**。3方式が完全一致する中で、ファイルを開かずに判定できる唯一の方式（コスト最小）

**X4 Phase 1 対象期間の CLI version と type 分布**

- Phase 1（14日）の version: 0.147.0=265件 / 0.149.0=43件（全期間は6 version）
- (type, payload.type) の組: Phase1=**21種** / 全期間=**29種**
- **全期間にあって Phase 1 に無い組（8種）**: `event_msg|context_compacted` / `event_msg|entered_review_mode` / `event_msg|exited_review_mode` / `event_msg|mcp_tool_call_end` / `event_msg|patch_apply_end` / `event_msg|turn_aborted` / `response_item|tool_search_call` / `response_item|tool_search_output`
- Phase 1 にしかない組: 0件
- **採用**: 「Phase 1 観測 version のみ許可」という設計は上記8種を静かに取りこぼすので不採用。**パーサは version でフィルタせず `(type, payload.type)` の組をそれ自体で判定し、未知の組は安全にスキップしてログに倒す**

**X5 複数 session_meta ファイルの影響（設計を1つ壊した発見）**

- 該当ファイル **101件**（前回100件＋総数増分）
- 先頭 `session_meta.id` を全体に付与した場合の**誤帰属 user 発話: 全数 370件・うち Phase 1 内 305件**
- `(session_meta.id, timestamp, text_hash)` の **UNIQUE 衝突: 78件発生**（前回の「衝突0件」は先頭IDのみを使った測定であり、後続 meta 由来の発話まで含めると衝突する）
- **採用**: 「先頭IDをファイル全体に付与」は**不採用**。**各 user 発話を、レコード順で直前に出現した `session_meta` のセグメントの id に帰属させる**。これをしないと Phase 1 の発話の大半が誤帰属し、かつ一意キー衝突でデータが失われる

**X6 除外・dedup 後の実件数（Go/No-Go の分母）**

Phase 1（14日・evolve-anything cwd）、方式A/B とも同一:

1. 生の user 発話（両系統合計）: **769件**
2. 子セッション除外後: **503件**
3. 機構マーカー除外後: **373件**
4. dedup 後（X1 のチャネル制約付き T=100ms）: **259件**（T=0 の保守値では264件）

**最終分母 = 259件**。日数感度表（step3 と step4(T=0) の値）:

| 日数 | 対象ファイル | step1生 | step2子除外後 | step3機構除外後 | step4最終(T=0) |
|---|---|---|---|---|---|
| 7 | 60 | 270 | 257 | 209 | 179 |
| 14 | 227 | 769 | 503 | 373 | 264 |
| 30 | 296 | 1161 | 814 | 567 | 376 |
| 60 | 300 | 1173 | 826 | 575 | 382 |
| 90 | 300 | 1173 | 826 | 575 | 382 |

14日時点で既に200件超のため**日数の延伸は不要**。CC 側実測率4.48%を当てはめた期待陽性は約12件。

## 未決事項一覧（Phase 1 着手前に確認）

| 未決項目 | 解決する Phase | Phase 1 着手のブロッカーか |
|---|---|---|
| D5b: source 永続化方式 (a)/(b)/(c) の最終選択 | Phase 2 | No（Phase 1 は `source_path` prefix 判定で足りる） |
| D7: pj_slug 正規化方式 (a)/(b) の最終選択 | Phase 2 | No |
| D6: 日単位 transaction のロールバック粒度（ファイル単位 or 日単位） | Phase 3 | No |
| Go/No-Go 3値判定の閾値（≥5件 / 0〜1件）自体の妥当性 | Phase 1（最終判定時） | No（今回の実行はベースライン取得のみで判定を確定させないため、着手は妨げない。ただし移行後再測前にレビュアー確認が必要） |
| `pj_slug_fast` の将来統一計画の有無（D7 (a) が CC 側との二重管理になるか） | Phase 2 | No |
| ~~C-1 ベンチが本番 `utterances.db`/`ingest_state` に書く設計か~~ → **解消**（2026-08-23 頭の裁定: 本番に一切書かず隔離 DB のみ。C-1 参照） | — | **No**（Phase 1 の着手ブロッカーはゼロ） |

## Phase 構成の概要

本設計は3段階で実装する。**Phase 1（MVP）だけをまず作り、計測結果に基づいて Phase 2 以降に
進むかを判定する**。Phase 2/3 の詳細設計は Phase 1 完了後に着手する（いま網羅しない）。

| Phase | 目的 | 入口条件 |
|---|---|---|
| **Phase 1** | 「Codex ログから指摘が取れるか」の移行前ベースラインを取得する。**既存コードを変更しない** | 本 ADR の承認 |
| **Phase 2** | D2（extractor 共通化）着手＋全PJ展開（pj_slug 正規化・source 永続化の確定を含む） | Phase 1 完了（ベースライン取得） かつ Codex 移行後の再測で Go 判定 |
| **Phase 3** | 継続運用への配線（日次 ingest・`evolve --drain` 配線・朝の y/n 露出） | Phase 2 完了 |

## Decision

以下 D1〜D8 は Phase を問わず内容が確定した決定（D1・D8）と、Phase ごとに実装対象が
分かれる決定（D2〜D7）に分かれる。D2〜D7 の「いつ作るか」は各 Decision の見出しに明記する。

### D1. 新しいデータ置き場を作らない（全 Phase 共通・第1版から変更なし）

#379 Step 1 の新設凍結（新 store / observability section / advisory proposal adapter /
weak_signal channel の追加停止。単一ソースは `scripts/lib/shrink_freeze.py`）に抵触させない。
Codex 由来の発話も **既存の utterance_archive へ流す**。新しい表示欄も作らない。

### D2. extractor は「source 別 parser はイベント列まで、reducer は1本」に統一する（**裁定Aにより Phase 2 の先頭へ移動**。設計内容は第2版から変更なし）

**裁定A（頭）**: codex は「MVP に過大、Go 後に共通化すべき」、tacchi は「純リファクタなので No-Go
でも残せばよい」と意見が割れた。頭の裁定は codex 寄りで、**D2 の実装は Phase 2 の先頭に置く**。
理由: Phase 1 の価値は「捨てられること」にある。純リファクタでも既存コードに触れば回帰リスクと
レビューコストが発生する。この裁定により No-Go 時の後始末が「一時ディレクトリを消すだけ」になり
（C-3 参照）、tacchi Must-5 の「後始末の自己矛盾」も同時に解消する。

以下の設計は Phase 2 着手時にそのまま使う契約として確定しておく（第2版の設計内容を維持）。

第1版は `extractor_cc` / `extractor_codex` の2系統がそれぞれ最終 `Utterance` を作る案だったが、
これは `design-before-fanout.md`（同型処理の共通部品を先に設計する。N 回独立実装は同じ欠陥を
N 回再生産する）に反する。codex レビューも同じ理由で reject した。

現行 `extract_utterances()`（`extractor.py:244-395`）を読むと、以下が1関数に一体化している:

1. 行パース（`json.loads`）
2. cwd 由来 pj_slug 確定（`extractor.py:294-297`）
3. sidechain 除外（`isSidechain` 行単位、`extractor.py:309-310`）
4. role 判定・assistant/user 分岐（`extractor.py:312-322`）
5. `pending_tool_names` の蓄積とリセット（prev_action 生成、`extractor.py:277,316-318,366-367`）
6. harness マーカー除外（`extractor.py:362-363`）
7. 画像プレースホルダ strip（`extractor.py:346-350`）
8. `source_kind` 分類（dialogue/long_paste/excluded_pj、`extractor.py:377-382`）
9. `Utterance` 生成（`extractor.py:384-395`）

このうち **1〜4（行パース〜role 判定）は source 固有**（CC と Codex で JSONL 構造が根本的に異なる。
Codex は `function_call` がメッセージ内ネストでなく独立したトップレベル行、かつ M1/M2/X1 の
二重表現・M3 の子ファイル分離という CC に無い構造を持つ）。**5〜9（prev_action 集約〜Utterance 化）は
source 非依存**であるべきだが、現行実装は分離されていない。

**確定する契約**:

- source 別 parser（`parser_cc` / `parser_codex`）は、生ログ1行 → 正規化イベント（`role: str, text: Optional[str], tool_names: List[str], timestamp: str, cwd: Optional[str], session_id: str, raw_type: str`）のイテレータを返すところまでを担当する。**Utterance を作らない**。Codex 側 parser はここで M1/X1/M3 を解決する: M1 の機構マーカーは除外理由付きでカウントし reducer に渡すか、parser 内で除外し統計を返すかは実装時に確定する。X1 の二重表現は parser 段階でチャネル制約付き `(file, text_hash)` dedup（T=100ms）を適用してから reducer に渡す。M3 の子ファイルは ingest 層（D4 参照）でファイル単位除外するため parser 自体は関知しない
- reducer（1本、`extractor.py` の 5〜9 相当を抽出して切り出す）は、正規化イベント列を受け取り `pending_tool_names → prev_action` の集約、harness/画像/source_kind の判定、`Utterance` 化を行う。**この reducer は CC/Codex で共有し、source ごとに複製しない**
- 既存 `extract_utterances()` は `parser_cc` + reducer の組み合わせとして振る舞い不変のまま再構成する

**完了条件（tacchi Should反映・第1版の未解決質問1への回答）**: extractor の回帰テストは
既に4本実在する（`scripts/lib/tests/test_utterance_extractor.py` / `test_utterance_store.py` /
`test_utterance_query.py` / `test_utterance_ingest.py`）。golden/snapshot 水準かは未確認のため、
D2 切り出しの完了条件を**「既存4本の緑維持 ＋ 実コーパス dogfood 1本」**とする
（`learning_synthetic_fixture_false_confidence` の判例に従う）。

### D3. 機構発話の除外は9種のマーカーで判定し、除外述語を CC 側と単一関数に集約する（**Phase 1**・第1版から拡張、第2版の「6種」を訂正）

Phase 1 で作る理由: 機構マーカーを除外しないと自動挿入文が「ユーザーの指摘」として C-0 の
計測に混入し、指摘率という Phase 1 の唯一の判定材料が意味を失う。Phase 1 の必須構成要素。

M1 実測により、Codex の機構混入は `developer` role だけでなく、`response_item.message(role=user)`
の先頭タグとして下記の**9種**が出現する（第2版は「6種」+「低頻度3種」と分けて書いていたが、
本文中の判定対象と件数が食い違っていたため1つの表に統一する）:

| 先頭タグ | 件数 | 出現系統 |
|---|---|---|
| `recommended_plugins` | 584 | response_item のみ |
| `task-notification` | 491 | response_item のみ |
| `command-name` | 141 | response_item / event_msg 共通 |
| `local-command-stdout` | 133 | response_item / event_msg 共通 |
| `command-message` | 27 | response_item / event_msg 共通 |
| `skill` | 26 | response_item のみ |
| `environment_context` | 7 | response_item のみ |
| `user_action` | 2 | response_item のみ |
| `image` | 1 | response_item のみ |

このうち `command-name` / `local-command-stdout` / `command-message` は、CC 側の
`_HARNESS_MARKERS`（`extractor.py:52-59`: `<system-reminder` / `<command-name` /
`<local-command` / `Caveat:` / `[Request interrupted` / `This session is being continued`）と
**同種の露出**である。CC 側は `<command-name` のようにマーカー文字列として先頭タグを判定しており、
Codex 側も同じ発想（先頭タグ文字列判定）で一致させられる。

**確定する契約**: 除外判定は CC/Codex 共通の単一関数（Phase 1 スクリプト内、または Phase 2 で
reducer 側 `rl_common.detection` へ寄せる。現行 `_is_machinery_prompt_shared()` が
`rl_common.detection.is_machinery_prompt` へ委譲する既存パターン（`extractor.py:196-215`）を
踏襲）に集約し、CC 用マーカーリストと Codex 用マーカーリストを1箇所で管理する。2箇所に
同じ判定を書かない。

`developer` role の除外は維持する（人間発話ではなくシステム注入。CC の harness マーカー除外と対称）。

**未知 role の扱い**（tacchi Should 反映）: role を allowlist（`user` のみを人間発話として許可）
で判定し、未知 role は user 扱いしない。除外件数は observability に surface する（silence ≠ evaluated）。

### D4. sub-agent 除外はファイル単位判定を構造的に正しい設計として確定する（**Phase 1**・第1版から確定）

Phase 1 で作る理由: 子セッションの発話は D3 と同じ「ユーザー指摘の誤認」事故（この場合は
sub-agent の発話をユーザー発話として誤計測する）を起こすため、Phase 1 の必須構成要素。

第1版は E18 未実測のため「暫定でファイル単位」としていたが、M3 の全数実測により以下が
**構造的事実**として確定した:

- Codex の sub-agent マーカー（`sub_agent_activity` / `inter_agent_communication_metadata`）は
  会話本文を含まない発火ログにすぎず、**子セッションの発話は別ファイルに存在する**
  （`agent_thread_id` が別ファイルの `session_meta.id` と一致）
- CC の「同一ファイル内で `isSidechain: true` 行を除外する」方式とは構造が異なるため、
  CC 側の行単位除外ロジックを Codex に転用することはできない

**確定する判定方法**: 全ファイルの `sub_agent_activity.agent_thread_id` の集合を作り、
あるファイルの `session_meta.id`（先頭）がこの集合に含まれれば、そのファイルを子セッションとして
ingest 対象外にする。**参照集合は Phase 1 対象ファイル（227件）に限定してよい**（X2 実測: 全677
走査でも Phase1限定走査でも子判定結果は同一90件、境界跨ぎ0件）。

**Consequences に明記する数値**: 全数実測で **153/675 = 22.7%** のファイルが子セッションと判定され
除外される。tacchi の別集計（213/675 ファイル・745/6354 user record）との差分は**母集合の違いによる
ものであり解明済み**（上記 Evidence 参照。親ファイル数と子ファイル数を混同していただけで矛盾ではない）。

### D5a. 識別子とセグメント帰属（**Phase 1**）

**session_id**: 論理 UNIQUE には **`session_meta.id`**（ファイル名 UUID と 675/675 一致）を採用する。
`session_id` フィールド（`payload.session_id` 等）は `id` と 19%（148/775）で乖離するため使わない。

**dedup key**: 二重表現の dedup は X1 実測に基づき、**「同一ファイル・同一 text_hash・かつ
response_item ↔ event_msg の異チャネル間のみ」に限定した 1:1 マッチング、T=100ms**を採用する。
グローバル閾値方式（チャネル区別なし）は偽統合を生むため不採用（X1 参照）。

**マッチング規則（決定論・実装者が一意に実装できる形で定義する。codex v3 [Must] 反映）**:
実データには同一 `(file, text_hash)` で各チャネルに複数候補を持つ group が **53件実在する**
（codex が全679ファイルで実測）ため、走査順で結果が変わらないよう次の規則で固定する。

1. `response_item` 側を基準集合とし、`(timestamp, line_no)` の**昇順**で走査する
2. 各要素について、**同一 file・同一 text_hash・未マッチ**の `event_msg` 側候補のうち、
   **`|delta|` が最小のもの**を選ぶ
3. `|delta|` が同値の候補が複数ある場合は **`line_no` が小さい方**を選ぶ
4. `|delta| > T`（T=100ms）の候補は選ばない（マッチさせない）
5. マッチした対は双方を**消費済み**にする（1:1 を保証）
6. どちらのチャネルにも残った未マッチ要素は、**それぞれ独立した発話として残す**

**「偽統合が構造的に発生しない」という第3版初稿の記述は撤回する**。正しくは
「同一チャネル内を比較しないため、同一文言が同一チャネルに繰り返し並ぶケースでは誤統合しない。
異チャネル間で候補が複数ある場合（実測53群）は上記 1〜6 の規則で一意に決まる」である。

**セグメント帰属（X5 実測により第2版から訂正）**: 複数 `session_meta` を持つファイル（101件）で
「先頭 `session_meta.id` を全体に付与」する方式は、**user 発話305件を誤帰属させ、UNIQUE キー衝突を
78件発生させる**ことが実測で判明した。**確定する方式**: 各 user 発話を、レコード順で直前に出現した
`session_meta` セグメントの `id` に帰属させる（先頭固定でなく、ファイル内で `session_meta` が
切り替わるたびに以降の発話の帰属先を更新する）。この方式は resume/fork いずれのケースにも
中立に対応でき、複数 `session_meta` の発生原因（resume か fork か）を判別する必要自体をなくす。

**未帰属発話の扱い（codex v3 [Should] 反映）**: 「直前の `session_meta`」が存在しない発話
（ファイルに `session_meta` が1つも無い / 最初の `session_meta` より前に user 発話がある）は、
**user 発話として採用せず、件数を実行レポートに surface する**（未知 type を安全に skip する
方針と同じ扱い。黙って落とさない）。

実測（codex が全679ファイルを走査・2026-08-23）では **`session_meta` 無しで user 発話を持つ
ファイル = 0件 / 最初の `session_meta` より前に user 発話を持つファイル = 0件** であり、
現時点の Phase 1 を壊す欠陥ではない。この規定は将来の形式変更に対する保険として置く。

### D5b. source の永続化（**Phase 2**・反映必須4への対応。第1版の D5 後半を分離）

Phase 1 は `source_path` の prefix（`~/.codex/sessions/` か `~/.claude/projects/` か）から read 時に
判定すれば足り、スキーマ変更は不要。列を追加するかどうかの決定自体を Phase 2 に送るが、
選択肢と推奨は以下に書いておく。

現行 `Utterance`/`utterances` テーブルは
`role` / `tool_names` / `source` / `model` を保存できない（上記「既存スキーマとの不整合」参照）。
選択肢を検討した:

- (a) 列を1本追加する（`source TEXT`）: 既存 reader（`query_utterances*` 系、`correction_semantic`
  経路）は SELECT する列を明示していない場合は影響を受けないが、`Utterance` dataclass の
  frozen フィールド追加は全生成箇所（parser/reducer 双方）の呼び出しを変える。**`CREATE TABLE
  IF NOT EXISTS` は既存テーブルに列を増やさないため ALTER/migration と `_COLUMNS` 更新が必須**
  （`store.py:31-47`）。これを Phase 2 の入口条件とする
- (b) `source_kind` の値空間を拡張する（例: `dialogue_codex`）: 既存 `source_kind` は
  `dialogue` / `long_paste` / `excluded_pj` という「発話の性質」の分類軸であり、
  「どのCLIから来たか」という直交する軸を混ぜると `EXCLUDED_PJ_SLUGS` 判定等の既存分岐が
  組み合わせ爆発する
- (c) `source_path` の prefix から read 時に導出する（`~/.codex/sessions/` vs
  `~/.claude/projects/`）: 新規列・スキーマ migration が不要。**既存 reader を一切変更せず、
  区別が必要な箇所だけが `source_path` を見て判定する**。**codex 実コード確認済み（Should反映）**:
  `query.py` は明示列を SELECT し下流は返却 dict を読むため、`source_path` 導出方式なら
  `query.py:21-29,41-69` に変更は不要

**推奨: (c)**。理由は、(a)(b) はスキーマ migration と全 reader の契約変更を要し
`design-before-fanout.md` の「共通部品の契約を破ると両系統に同じ欠陥が独立再生産される」
リスクを新たに作る一方、(c) は物理 PK に既に含まれる `source_path` から導出可能で
migration 不要。ただし **これは推奨であり未決**（上記「未決事項一覧」参照。role/tool_names/model の
永続化要否は本 ADR で結論を出さない）。

`role` / `tool_names` / `model` については、**本 ADR では永続化しない**（現行 `prev_action`
文字列で tool 名相当は既に賄われている。`role` は human 発話のみを保存する現行契約と矛盾しない。
`model`/`effort` は柱2の指摘判定に現時点で使われていないため、必要になった時点で別途スキーマ
拡張を検討する）。

### D6. 遡り取り込みは日単位で束ね、単位ごとに永続化する（**Phase 3**・第1版から変更なし。ただし D7 とは独立事項として明記）

Phase 3 で作る理由: Phase 1 は 1PJ・N 日の一回限りの実行で足りるため、継続 ingest 化
（日単位の transaction・再開判定）は不要。Phase 1 は単発実行スクリプトでよい。

E10 により過去分の取り込みが可能。`persist-progress-incrementally.md` に従い、
**日単位（YYYY/MM/DD ディレクトリ）を1単位として、単位ごとに永続化＋再読検証してから次へ進む。**
最後にまとめて書く設計にはしない。再開判定は進捗マーカーでなく**実体（既存の一意キー集合）を
読み直して突合**する。

**現行 API との差分（Should 反映）**: 現行 `ingest_pj_dir()`（`ingest.py:51-126`）は PJ ディレクトリ
単位で glob し、`ingest_state(source_path, mtime, line_offset)` で増分判定する（ファイル単位の
mtime/offset 追跡であり日付ディレクトリ単位の transaction 境界を持たない）。Codex ingest を
日単位で束ねる場合、**この日単位束ねは Codex 側 ingest 関数の呼び出し粒度として実装し、
CC 側の `ingest_state` の意味論（source_path 単位）は変更しない**。1日分の transaction が
途中失敗した場合のコミット状態（ファイル単位でロールバックするか、日単位でロールバックするか）は
実装時に確定する（未決事項一覧参照）。

### D7. pj_slug 帰属（**Phase 2**・M6 の 28.9% 孤立への対応）

Phase 2 で作る理由: Phase 1 は evolve-anything 1PJ 固定なので、cwd の完全一致（または
`evolve-anything` 文字列を含むかの部分一致・X3 と同じ日付ディレクトリ判定）でフィルタすれば足り、
worktree 正規化は不要。全PJ展開する Phase 2 で、正規化を怠ると孤立が起きる問題として
入口条件に明記する（Phase 2 セクション参照）。

既存 `pj_slug_fast` は **変更しない**（CC 側への波及を避ける）。

**未決（2つの選択肢。推奨あり）**:

- (a) Codex ingest の write 時のみ、repo-root 探索を併用する正規化関数を通す。**推奨**:
  M6 の孤立（28.9%）を解消できる。デメリットは Codex ingest 固有の分岐が増えること
- (b) `wt-*` パターンを追加マーカーとして `pj_slug_fast` に登録する: CC 側にも波及するため
  リスクが高い（`pj_slug_fast sibling worktree 限界` pitfall・#593→#602 が既に踏んだ領域に
  さらに変更を加えることになる）

**放置した場合の Consequences**: worktree 由来の Codex 発話が本体 PJ に紐づかず、朝の y/n
提示に出ない（孤立 slug のまま埋没する）。

### D8. ADR-052 との関係（全 Phase 共通・第1版から変更なし）

ADR-052 の以下は**維持**する（費用前提の変化と独立に成立する契約）:

- lane は `<issue>-<slug>`、1 owner / 1 writer、owned_paths 非重複
- code の SoT は commit、handoff 証拠は git-common-dir 外部 metadata
- stage は明示 path ＋ cached diff 全体の allowlist 検証
- **merge / release / Issue close は人間 authority**

ADR-052 の「Claude Code = primary executor / Codex = opt-in」は費用前提の失効により
**別 ADR で改めて扱う**（本 ADR のスコープ外）。本 ADR は観測機構のみを決める。

**本 ADR は実装主戦場の移行を承認するものではない。** 観測が繋がることと、
どちらで実装するかは独立の意思決定である。

## Non-goals（反映必須6への対応）

本 ADR は「観測の入力を増やす」施策である。以下は**解決しない**:

- **観測→作用の変換率**: MEMORY `learning_measurement_layer_diagnosis.md` /
  `project_pillar1_v2_and_proposal_lanes.md` によれば、現状の変換率は 4.3%、真の詰まりは
  `last_skill 2/208` にある。**入力ソースを増やしても、詰まりが下流（変換〜採用）にあるなら
  産出は増えない可能性がある**。**この可能性は C-0 で較正するが、下流の変換率（weak signal →
  confirmed → skill/rule 採用）そのものは測定しない**（tacchi Should反映・下記 C-0 参照）
- ADR-052 の primary/opt-in 役割配分の見直し（D8 で別 ADR に切り出し済み）
- extractor リファクタ（D2）に伴う CC 側の振る舞い変更（振る舞い不変が前提）

## Phase 1（MVP）

**目的**: 「Codex のログから指摘が取れるのか（ゼロではないのか）」の**移行前ベースライン**を
最小コストで取得する。今回の実行結果は Go/No-Go を確定させない（裁定B）。

**入口条件**: 本 ADR の Decision（D1・D3・D4・D5a・D8）が承認されること。

**実装方針（裁定A）**: **既存リポジトリのコードを一切変更しない使い捨てスクリプト1本**として
実装する。Codex サンプルは既存 `Utterance` dict 相当へ変換し、`run_daily_judge` の DI パラメータ
（`judged_path` / `weak_signals_path` / `idioms_path` / `utterances`）へ渡すだけでよい（C-0 参照）。

**スコープ**:

- 対象: evolve-anything 1PJ（cwd に `evolve-anything` を含む Codex rollout ファイルのみ。
  X3 の日付ディレクトリ判定方式）
- 期間: 直近 **N=14 日**（M9/X2 の実測に基づく推奨値。X6 で 200件超の分母を確保できることを確認済み）
- 対象セッション種別: **親セッションのみ**（D4 のファイル単位除外で子セッションを外す）
- 実装するもの: D3（機構マーカー9種除外）・D4（子セッションファイル単位除外）・D5a
  （`session_meta` セグメント帰属 + チャネル制約付き dedup）・C-0（較正・移行前ベースライン取得）・
  C-3 の凍結ゲート検証条件

**意図的に含めないもの**（1PJ 固定・単発実行なので不要。後で足せる）:

- **D2（extractor リファクタ）**: 裁定Aにより Phase 2 の先頭へ移動。Phase 1 のスクリプトは
  D2 と同等の parser/reducer 分離を*スクリプト内で構造的に*行うが、既存 `extractor.py` には
  一切触れない
- D7 の pj_slug 正規化。M6 の 28.9% 孤立問題は**放置せず Phase 2 の入口条件として明記**する
  （下記 Phase 2 参照）。Phase 1 は 1PJ 固定なので worktree 正規化の要否そのものが発生しない
- D5b の `source` 列追加。Phase 1 は `source_path` prefix 判定で足りる。
  列を足すか否かの決定は Phase 2 に送る
- D6 の日単位永続化＋再読検証。Phase 1 は 1PJ・N=14日の一回限りの実行なので単発スクリプトで
  よい。継続 ingest 化は Phase 3

**完了条件（裁定Bにより第2版から変更）**: C-0 較正が完走し、**移行前ベースライン**（Codex 側
指摘率・分母259件）が記録されること。C-1〜C-3（本 Phase のスコープに対する実機ベンチ・変異表・
副作用検査）が全て通ること。**Go/No-Go の確定は Phase 1 の完了条件から外す**（下記参照）。

### Phase 1 のスコープと Go/No-Go 閾値

**裁定Bにより、本 ADR 承認後の最初の C-0 実行結果は「移行前ベースライン」として記録するのみで、
Go/No-Go 判定には使わない。** 本判定は、Codex を主力にした後のログが2〜3週間分貯まってから、
同じ較正の器で再測して行う。

**判定式**: `Codex 側 correction positives / Codex 側対象 user turns`（**判定結果が確定した発話数**
を分母とする。判定失敗・parse failure・omitted verdict は分母から除き、失敗率として別表示する。
codex Must7/tacchi Must-2 反映）と、CC 側の同一判定器・同一分母セマンティクスの指摘率を比較する。

**比較対象（M10 実測・CC 側ベースライン）**: evolve-anything の指摘率 **4.48%（23/513）**。
`correction_judged.jsonl`（keyed 5,372行を分母）× `correction_idioms.jsonl`（分子）の決定論 join で
算出済み（LLM 呼び出し不要）。**全体率は 7.32% ではなく正しくは 7.43%（399/5,372）**（M10訂正・
下記参照）だが、**PJ 別の 4.48% は keyed のみの集計であり無傷**。

**Go/No-Go 判定（3値化。codex Must9・tacchi Must-4 反映。第2版の「1.5%以上」という絶対閾値は撤回）**:

- **Go**: Codex 側陽性 **≥5件** かつ、**検出された陽性の実物をユーザーが目視し、過半が実訂正であること**
- **No-Go**: Codex 側陽性 **0〜1件**
- **保留（再測）**: 陽性2〜4件
- 補助指標として Wilson 片側95%下限を併記してよい（参考値・頭の実測: CC 側 4.48% の Wilson 片側95%
  下限 = 3.20%。Codex 側 n=259 で下限1.5%を超えるには陽性7件が必要）
- **この判定基準自体はまだレビュアーの妥当性確認を経ていない**（未決事項一覧参照）。移行後の
  再測前にレビュアー（tacchi/codex）へ確認する

**join 実装時の必須ステップ**（M10 で実測確認済みの落とし穴）: `judged.jsonl` の
`source_path` から encoded pj dir 名を取り出し、`idioms.jsonl` 側の bare slug 集合に対して
**後方一致**で照合する。「最後のハイフンで切る」実装は `evolve-anything`→`anything` のような
誤爆を起こし指摘率が 0.00% になる（M10 で実際に発生した事故）。Codex 側の C-0 実装でも
同型の join を書くため、この正規化を必ず踏む。

**今回の実行結果の扱い（裁定B）**: 今回どんな数字が出ても Phase 1 の成果物（読み取り専用スクリプト・
較正の器）は捨てない。No-Go/Go いずれの判定も確定させず、「移行前ベースライン: N%（X/259）」として
MEMORY に記録するのみとする。

## Phase 2（全PJ展開）

**入口条件**: Phase 1 完了（移行前ベースライン取得）かつ、Codex 移行後の再測で Go 判定。

**スコープ**: D2（extractor の parser/reducer 分離、既存コードへの実装反映）・D7（pj_slug 正規化。
M6 の 28.9% 孤立解消）・D5b（`source` の保存方法確定、上記 (a)(b)(c) の選択。(a) を選ぶ場合は
ALTER/migration + `_COLUMNS` 更新を必須とする）に着手する。対象を evolve-anything 1PJ から
全 PJ へ拡大する。

**意図的に含めないもの**: D6 の継続 ingest 化・`evolve --drain` 配線・朝の y/n 露出は
Phase 3 に送る。Phase 2 は「全 PJ の Codex ログを取り込める状態にする」までで、
日次自動化はまだしない。

**完了条件**: 本 ADR は Phase 2 の詳細設計（Decision・Test Plan の変異表）を含まない。
Phase 1 完了後、Phase 2 着手前に別途設計する（レビュー要否は design-review-gate.md の
複雑さ基準に従う）。

**ただし下記の golden replay 契約だけは本 ADR で確定する**（codex v3 [Must]「裁定A により
Phase 1 の使い捨て実装と Phase 2 の本番実装の同値性を保証する検査が抜けた」への対処）:

裁定A により Phase 1 は使い捨てスクリプトで、Phase 2 は本番コードへの再実装になる。
**Go 判定に使った抽出器と本番抽出器の挙動が変わると、判定の根拠そのものが無効になる**。
これを防ぐため:

- **Phase 1 の完了成果物に、次の3点を JSON で出力・保存することを含める**（一時ディレクトリでは
  なく、リポジトリ管理下の fixture として残す。No-Go でも残す＝裁定Bの「捨てないもの」に含める）
  1. 対象ファイル一覧とその内容ハッシュ（入力の固定）
  2. 正規化後のイベント列（source 別 parser の出力に相当）
  3. 最終 259件の**一意キー一覧**（`(session_meta.id, timestamp, text_hash)` のセグメント帰属版）
- **Phase 2 の完了条件に追加**: 本番実装が上記1の入力から、**上記3と同一の一意キー集合**を
  再生すること（集合として完全一致。順序は問わない）。差分が1件でもあれば Phase 2 は未完了
- 差分が出た場合、**どちらが正しいかを判断してから**進める（本番実装のバグか、Phase 1 の
  使い捨て実装のバグか。後者なら Go 判定の分母が変わるため再判定が必要）

## Phase 3（継続運用への配線）

**入口条件**: Phase 2 完了。

**スコープ**: D6（日単位永続化・再読検証を伴う継続 ingest 化）・`evolve --drain` への配線・
朝の y/n への露出。

**意図的に含めないもの**: 特になし（Phase 3 が本 ADR の観測機構整備の最終段階）。

**完了条件**: 本 ADR は Phase 3 の詳細設計も含まない。Phase 2 完了後に別途設計する。

## Test Plan（Phase 1 のスコープに対して書く。Phase 2/3 の変異は各 Phase 着手時に設計する）

### C-0. 較正（**実装着手前に実施する**。codex Must「較正が実行不能」への修正版）

**第1版の問題**（codex Must）: 既存 `judge_runner.py` の dry-run（`judge_runner.py:275,371` 相当）
は LLM を呼ばず件数・トークン見積もりを返すだけで、「ユーザー指摘と判定される件数」は
そもそも取得できない。

**修正した手順**:

1. Phase 1 のスコープ（evolve-anything 1PJ・直近 N=14日・親セッションのみ）に絞った
   Codex ログから、read-only スクリプトで user 発話をサンプル抽出する
   （D3/D4/D5a の除外・帰属・dedup を適用した「純粋な人間発話」のみ。ストアには書かない）
2. サンプルを `judge_runner.py` の**実判定経路**（`run=True` 相当。LLM を実際に呼ぶ）で走らせる。
   固定サンプル・判定モデル名・prompt fingerprint・再判定回数を実行前に記録する
   （`llm-batch-guard.md` に従い件数とトークン見積もりを事前にユーザーへ提示してから実行する）
3. **CC 側ベースラインは既存ストアの join で取得済み**（M10・LLM 不要）: evolve-anything の
   指摘率 4.48%（23/513）。新規実行時は再現性確認のため同じ join を再実行し、値が動いていないか
   確認する（`judged.jsonl` の `source_path` encoded 名 → `idioms.jsonl` の bare slug へ
   後方一致で正規化する。「最後のハイフンで切る」実装は誤爆するので使わない。M10 参照）
4. **判定式**（絶対件数比から変更）: `correction positives / 対象 user turns`（判定結果が確定した
   発話数を分母とする）を CC/Codex で比較する。**下流の変換（weak signal → confirmed → skill/rule
   採用）は測らない（Non-goals）**（tacchi Should反映。第2版の「可能であれば下流まで追う」という
   柔らかい但し書きは撤回した）
5. **本較正の隔離**（codex Must10・tacchi Must-3 反映。最重要の安全策）: judge の書込先は DB では
   なく **JSONL 3本**（`judged_path` / `weak_signals_path` / `idioms_path`）で、`run_daily_judge`
   にこれらと `utterances` の DI パラメータが実在する（`judge_runner.py:259-273`）。**この DI で
   3 path すべてを同一の一時ディレクトリへ向け**、実行前後で本番3ストアの byte hash が不変で
   あることを検査する実行契約にする。judge は LLM 呼び出し前に judged path へコスト予約を書く
   （`judge_runner.py:511-540`）ため、その書込先も隔離対象に含む。**これを曖昧にしたまま実装すると
   較正が本番 `correction_judged.jsonl` / `correction_idioms.jsonl` を汚染し、比較対象である
   CC ベースライン 4.48% 自体が動く（自分の物差しを自分で汚す）事故になる**
6. **今回の実行結果はベースライン記録のみ**（裁定B）: Go/No-Go を確定させない。移行後のログで
   再測してから判定する

**再測の実行契約（codex v3 [Must]「責任のない先送り構造」への対処。2026-08-23 頭の裁定）**:
「2〜3週間後」という曖昧な条件を廃し、**実行条件・実行者・判定者・期限・打ち切り条件**を確定する。

| 項目 | 確定内容 |
|---|---|
| 移行起点 | **2026-08-23**（Codex 契約変更＝実装主戦場化の決定日） |
| 実行条件 | 移行起点以降のログに Phase 1 と同一の抽出をかけ、**分母が 259件（今回と同水準）に達した時点**。日数ではなく**件数**で定義する（日数は稼働量に依存し、待っても貯まらない場合に無限待ちになるため） |
| 実行者 | オーケストレーター（頭）。分母の到達確認は決定論スクリプトで随時可能 |
| 判定者 | **ユーザー**（Go 条件の「陽性の実物を目視し過半が実訂正」は人間にしか判定できない） |
| 期限 | **2026-09-30** |
| 期限までに分母未達の場合 | 「Codex の実利用量が想定に達しなかった」と結論し、**Phase 2 を見送り本 ADR を Superseded にする**（無期限保留にしない） |

**「保留（再測）」に落ちた場合の契約（codex v3 [Must] 反映）**:

- 再測は**最大2回まで**（初回＋再測2回＝計3回）
- 各再測の実行条件は上表と同じ（分母 259件の追加蓄積ごとに1回）
- **2回目の再測でも保留（陽性2〜4件）だった場合、最終裁定はユーザーが行う**。頭は
  「Go として Phase 2 に進む / 打ち切って Superseded にする」の二択を、それまでの全実測値を
  添えて提示する。**頭が独断で3回目以降の再測を続けない**

**分母セマンティクスの整合（codex Must7・tacchi Must-2 反映）**: CC 側の513は「判定にかけた発話」
であり、実判定には omitted verdict / parse failure / assistant-only skip がある。抽出件数を
そのまま分母にすると判定失敗まで陰性として数えてしまう。**両側とも「判定結果が確定した発話数」を
分母にし、`corrections / (corrections + non_corrections)` とする。失敗率・欠落率は別表示する**
（根拠 `scripts/lib/correction_semantic/judge_runner.py:535-581`）。CC 側の513は `run_daily_judge`
の母集団フィルタ（**dialogue のみ / tracked_projects / age cutoff 90日**、`judge_runner.py:259-272`
の docstring）を通過した後の件数であり、**Codex 側も同等の分類（long_paste 除外含む）と cutoff を
通した後の件数を分母にする**（tacchi Must-2）。

**較正の自己成就性への回答（Rationale・codex Must2 反映）**: 判定器は CC の会話を基準に育った
LLM Judge であり、Codex ログに対しても同じ判定器を使う以上「Codex の方が指摘の質が高いか/低いか」
を測ることはできない。本較正の目的は **「Codex ログから同判定器で指摘がゼロでなく取れるか」**
の確認に限定する。指摘の質そのものの比較は、より長期の運用実績（本番採用後の corrections.jsonl
蓄積）を待つ必要があり、本 ADR のスコープ外とする。

### C-1. 実機 E2E ベンチ（`transcript-store-bench.md` 必須）

pytest fixture ではなく**実データで1回完走**させ、次を assertion してから完了報告する:

- wall time（phase 単位 timeout + 毎件 `print(flush=True)` で進捗を吐く）
- DB size 増分
- row 数
- **既知の O(N) 破綻経路を全量で実走しない**（`--max-files N` サンプリングを併用）

Phase 1 のスコープ（evolve-anything 1PJ・直近14日・227ファイル・X2 実測）は
E2（全期間677ファイル）や E16（CC 側現規模）よりはるかに小さいが、**比率だけで
「軽いから大丈夫」と判断しない**。実測値で判断する。

**隔離方式の確定（頭の裁定・2026-08-23。codex Must11・tacchi Must-5 を閉じる）**:
**C-1 は本番 `utterances.db` / `ingest_state` に一切書かない。隔離した一時 DB のみを使う。**

根拠: 裁定A により Phase 1 は既存コードを改変せず、本番 ingest 経路（`scripts/lib/utterance_archive/ingest.py`）
そのものを呼ばない。Phase 1 の使い捨てスクリプトは自前で一時 DB を作成し、そこへ書く。
したがって「本番ストアへ書いてしまった場合の削除手順」は**そもそも不要**になる。

**実行契約**（C-0 と同じ形にそろえる）:

- 一時 DB / 一時 JSONL 3本を、単一の一時ディレクトリ配下に作る
- 実行前後で本番 `utterances.db` の **byte hash が不変**であることを検査する
- 実行前後で本番 `correction_judged.jsonl` / `correction_idioms.jsonl` /
  weak_signals ストアの **byte hash が不変**であることを検査する（C-0 と共通）
- No-Go / 保留のいずれでも、後始末は**その一時ディレクトリを削除するだけ**で完了する

**検査の実行契約（codex v3 [Should] 反映。「誰がいつ assert するか」を曖昧にしない）**:

- **assert する主体**: Phase 1 のスクリプト自身が、処理の**最初と最後**に本番ストアの hash を
  取り、不一致なら**非ゼロ終了する**（人間の目視確認に委ねない）
- **対象ファイルが存在しない場合**: hash を `None` として記録し、**「実行前も実行後も不在」
  なら合格・「実行前は不在だったが実行後に出現」は失敗**として扱う（新規作成も汚染に含める）
- **検査の順序**: judge は LLM 呼び出しの**前に** `judged_path` へコスト予約を書く
  （`scripts/lib/correction_semantic/judge_runner.py:511-518` で確認済み）。したがって
  事後 hash の取得は **judge の全処理が終わった後**に行う。予約書込だけが本番へ漏れる事故を
  この順序で検出できる

この裁定により、未決事項一覧の「C-1 ベンチが本番ストアに書く設計か」は **解消**（Phase 1 の
着手ブロッカーはゼロになった）。

### C-2. 検査の有効性を「壊して赤くする」で証明する（`verify-checks-by-breaking.md`）

追加するテストが実際に効くことを、**通したまま仕様を壊す変異**で確認する。
①〜④を各1件が**下限**（網羅でも上限でもない）。

| 分類 | 変異 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|
| ① 要素を消す | `developer` role の除外条件を削除 | D3（機構発話を指摘と誤認しない） | reducer の role フィルタ |
| ② 意味を壊す（表現差） | 先頭タグ判定を正規形（`<command-name`等）のみに限定したまま、BOM・Unicode 全角スペース・改行を先頭タグの前後に混入させた表現には対応しない | D3（表現差クラス。①とは異なる経路を壊すため独立） | 正規形以外の先頭タグを持つ fixture |
| ③ 分散・入替 | tool 名の正規化を `function_call.name` だけから作る実装に差し替え、`call_id` の対応付けは検証しない | D2/D5a（**どの user turn の prev_action にどの tool call を帰属させるか**。codex 指摘: swap のみでは tool_names リストが不変なら緑のまま通る） | prev_action の user turn 帰属を固定した fixture |
| ④ 鮮度（検査の無効化に相当） | ベンチマークを新規隔離 DB でなく前回成果物に向け、ingest 処理を no-op 化する | C-1（今回の実行が row を生成したこと） | 既存 row 数だけを見る C-1 検査 |
| **陽性対照** | 意味を変えない書き換え（キー順序入替・空白差） | — | **緑のままであること**（陰性試験と混ぜて数えない） |

**codex 指摘を反映した追加の異種素通り経路（実装前に fixture を用意する）**:

| # | 素通り経路 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|
| 5 | `response_item` と `event_msg` の**両方**を実装し dedup せずに emit する | X1（二重表現を単一発話に正規化） | チャネル制約付き dedup key（T=100ms）の適用有無を検証する fixture |
| 6 | sub-agent 判定が `sub_agent_activity` だけを見て、トップレベル `inter_agent_communication_metadata` を見落とす | D4（子セッション判定の完全性） | 両マーカーを含む fixture ファイルでの除外判定 |
| 7 | `response_item` 側だけを実装し `event_msg.user_message` を丸ごと落とす | M1/X1（両系統からの発話取得） | event_msg のみに存在する発話が失われないことの assertion |
| 8 | `start_line` より前の tool call を再走査せず、追記後最初の user 発話の `prev_action` を常に None にする | 現行 CC extractor の契約（`extractor.py:256,365` は offset 前も文脈更新する）を reducer が壊さないこと | 増分 ingest 後の1件目 utterance の prev_action が非 None になる fixture |
| 9 | **（X5由来・新規）** 複数 `session_meta` ファイルで「セグメント帰属」を「先頭ID一律付与」に戻す | D5a（発話は自分のセグメントの session に帰属する） | **複数 `session_meta` を持ち、各セグメントに user 発話があり、セグメントごとに異なる期待 `session_id` を持つ fixture**（codex v3 [Must] 反映。第3版初稿は「単一 `session_meta` の fixture」と書いており、その fixture では正実装と変異実装の出力が一致するため**必ず緑のまま素通りした**。実測で誤帰属305件・UNIQUE衝突78件が起きる以上、これが赤くならない検査は無効） |

各変異は、適用パッチ・対象テスト名・期待する単独失敗・陽性対照の成功を実装時の成果物として
残す（実装前の本文書時点では「表」にとどまり、実際に赤くなることは未確認。**実装完了報告には
実行結果を添える**こと）。

さらに探索する入力クラス: 空白 / Unicode / 改行 / エスケープ / 巨大入力 / 実行順序 /
キャッシュ鮮度（古い成果物を検査して緑になっていないか）。
**未探索のクラスが残る限り完了扱いにしない。**

### C-3. 副作用（`verify-side-effects.md`）

- dry-run で store への書込がゼロであること（`pitfall_dryrun_stateful_store_write.md`）
- **ただし設計上意図された書込を dry-run 純度の名目で殺さないこと**
  （`pitfall_dryrun_purity_kills_designed_writes.md` / #505→#513 の再発防止）
- `~/.codex/` 配下への書込がゼロであること（read-only URI で開く）

**No-Go 後の後始末（裁定Aにより単純化）**: Phase 1 は既存コードを一切変更しないため、
No-Go 時の後始末は**一時ディレクトリを削除するだけ**でよい（C-0・C-1 とも隔離先のみを使う
ことが裁定で確定済み。C-1 参照）。
隔離できない場合は物理キー一覧を保存して transactional に削除・再読検証する手順を書く
（`scripts/lib/utterance_archive/store.py:32-56` / `ingest.py:181-182`）。

**凍結ゲートの検証条件（codex Must「検査対象の訂正」。第2版の記述をさらに訂正）**: 「新
observability section」の検査対象は `CULLED_OBSERVABILITY_SECTIONS`（表示抑制集合）**ではなく**、
live `_OBSERVABILITY_BUILDERS` と `FROZEN_OBSERVABILITY_SECTIONS` の**差分**である
（`scripts/lib/shrink_freeze.py:23-37,112-160,185-208`）。

**正しい検証条件**:

- `scripts/lib/shrink_freeze.py` の `store_registry` に宣言された active key の集合が
  実装前後で増えていないこと（`assert_no_new_keys` が対象とする集合そのもの）
- **Phase 1**: 本番 `utterances.db` に**一切書かない**こと（書込先は隔離した一時 DB のみ。
  実行前後の byte hash 不変で検査する。C-1 の実行契約を参照）。
  **Phase 2/3**: 書込先が既存 `utterances.db` の既存テーブル（`utterances` / `ingest_state`）
  のみであり、新テーブル・新 DB ファイルを作らないこと
- `_OBSERVABILITY_BUILDERS` と `FROZEN_OBSERVABILITY_SECTIONS` の差分が実装前後で増えていないこと
- 検証コマンド: `pytest -q scripts/lib/tests/test_shrink_freeze.py`

## Consequences

- **朝の y/n に出るのは Phase 3 完了後のみ。Phase 1/2 の間、ユーザー体験は一切変わらない**
  （codex Must3・tacchi Should反映。第2版の「C-0 が通った場合のみ柱2に入る」という記述は
  Phase 分割前の書き方であり過剰約束のため訂正した）
- D2（Phase 2 で実装）で reducer を共有する設計により、共通契約（正規化イベント形状）を破ると
  **両 source に同じ欠陥が同時に伝播する**リスクがある。これは「独立再生産」の代わりに
  「同時汚染」のリスクへ置き換わったことを意味し、reducer の契約テストを厚くする必要がある
- sub-agent 除外により Codex 側 **153/675 = 22.7%**（tacchi 差分は解明済み・母集合の違い）の
  ファイルが取り込まれない＝**取りこぼしが出る**。誤検出より安全側だが、**取りこぼし件数を
  surface する**こと（silence ≠ evaluated）
- pj_slug 未対応（D7 未決のまま Phase 2 に入った場合）だと worktree 由来の **28.9%** が孤立 slug の
  まま埋没し、朝の y/n に出ない
- X2 の「境界跨ぎ0件」は実測時点の事実であり、将来別期間で発生しない保証ではない。Phase 2 で
  対象期間を拡張する際は再確認する
- ADR-052 の primary/opt-in 判断は未決のまま残る（別 ADR）
- 本 ADR は下流の変換率詰まり（4.3%）を解決しない（Non-goals 参照）
- **裁定Bにより、Phase 1 承認後の最初の C-0 実行結果はいかなる数値が出ても Go/No-Go を確定させない**
  （移行前ベースラインとして記録するのみ）

## 行数

第2版 630行 → 第3版 782行（`wc -l` 実測 2026-08-23。codex v3 レビュー反映後の最終値）
