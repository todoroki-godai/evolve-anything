# ADR-055 追補: Phase "1.5"（1PJ限定・朝の y/n までの最小縦串）

- Status: **Draft（設計レビュー待ち。[Must] 残存中は実装着手しない）**
- Date: 2026-08-24
- Related: ADR-055（本追補の親 ADR）、#534
- 想定 issue: #534

## この文書の位置づけ（Phase 番号の整合。task 制約5への回答）

ADR-055 は Phase を「Phase 1（ベースライン取得の使い捨てスクリプト・**マージ済み**、
commit `87b31238`）→ Phase 2（extractor 共通化＋全PJ展開）→ Phase 3（継続運用配線・
`evolve --drain` 配線・朝の y/n 露出）」の3段に分けている。

本追補が作る縦串は、**Phase 3 の目的（朝の y/n 露出）の一部を、Phase 2 のスコープ
（全PJ展開・D2 extractor 共通化）を経ずに、evolve-anything 1PJ限定で先取りする**もの
であり、元の Phase 分けの「どの Decision を何のために作るか」の切り口とは軸が違う。

**採用する整合方法: Phase 1 と Phase 2 の間に新しい "Phase 1.5" を挿入する。**
Phase 2 は変更しない（D2 の extractor 共通化・D7 の pj_slug 全PJ正規化・D5b の source
永続化確定は、引き続き Phase 2 の入口条件どおり「全PJ展開するとき」に着手する）。
Phase 1.5 はこれらに**依存しない**縦串（後述）で、Phase 2 の前倒しではなく Phase 2 とは
別の独立した価値（「配線が実際に繋がるかを1PJで検証する」）を持つ。

理由:
1. ADR-055 の Phase 2 入口条件は「Phase 1 完了 かつ 移行後再測で Go 判定」だが、
   Go/No-Go の再測自体が「分母259件の蓄積」を待つ必要があり（再測の実行契約参照）、
   Phase 1.5 の縦串を先に通しておくと**再測に使う分母が daily-run の自動判定として
   自然に貯まる**（後述「計測」節）。Phase 2 の Go 判定を待ってから縦串を作ると、
   Go 判定に使うデータ自体が手作業計測のまま残る（circular）。
2. Phase 2 の D2（extractor 共通化）・D7（pj_slug 全PJ正規化）は「全PJ展開」のための
   投資であり、1PJ限定の縦串には**不要**（後述「最小の縦串」参照）。先に全PJ設計へ
   投資してから1PJで試すのは順序が逆（design-before-fanout の精神には反しないが、
   投資対効果の順序としては「小さく通してから広げる」が正しい）。
3. **Consequences の訂正が必要**: ADR-055 本文は「朝の y/n に出るのは Phase 3 完了後
   のみ。Phase 1/2 の間、ユーザー体験は一切変わらない」と明記している。Phase 1.5 は
   この文をそのまま破る（evolve-anything 1PJ限定で先に y/n に出す）。**この訂正は
   本追補の Decision に明記し、親 ADR 側にも「Phase 1.5 導入」を1行追記する**
   （親 ADR 本文の書き換えは本追補のスコープ外。実装 PR で親 ADR に追記する）。

## Non-goals（本追補が解決しないこと）

- D2（extractor の parser/reducer 共通化）: Phase 2 に残す。本追補は D2 と**別の**
  Codex 専用抽出コードを新規ファイルとして作る（既存 `extractor.py` に一切触れない —
  Phase 1 の「既存コードを変更しない」裁定Aの精神を維持する）
- D7（pj_slug の全PJ正規化・worktree 対応の恒久解決）: Phase 2 に残す。本追補は
  **単一 PJ（evolve-anything）だけを対象にした allowlist フィルタ**で足りる
  （後述 D1.5-3）
- D5b（`source` 列の追加可否）: Phase 2 に残す。本追補はスキーマ変更ゼロで実装する
  （後述 D1.5-2）
- D6（日単位 transaction・再開判定の厳密化）: 本追補は既存 `ingest_pj_dir` と同じ
  ファイル単位 mtime/offset 増分（`ingest.py:93-109`）をそのまま踏襲する。新しい
  transaction 粒度は作らない
- Go/No-Go の確定（ADR-055 裁定B）: 本追補は判定しない。Phase 1.5 が生む weak_signal
  は既存の人間 y/n を通るため、Go/No-Go を待たずに個別の正誤判断は人間が都度行える
  （後述「Consequences」参照）

## 現状の配線図（実測ベース。file:line 付き）

### CC 側（発話 → 朝の y/n。切れ目なく繋がっている）

1. `~/.claude/projects/*/*.jsonl` → `utterance_archive.ingest.ingest_all_projects()`
   （`scripts/lib/utterance_archive/ingest.py:129-184`）が `utterances.db` へ増分 INSERT
   （`store.py:169-181` の `insert_utterances`）。**呼び出し元は2箇所**:
   - `skills/evolve/scripts/evolve/phases_capture.py:147-154`（`evolve --drain` の
     apply 境界。`if not dry_run:` ガード下）
   - `scripts/lib/fleet/cli.py:345-354`（`_run_ingest`。`fleet ingest` CLI コマンド）
2. `bin/evolve-daily-run:104` が `subprocess.run(fleet + ["ingest"])` で上記(1)を
   毎朝 launchd 経由で叩く（`bin/evolve-daily-run:1-8` の docstring）
3. `bin/evolve-daily-run:143-160` が続けて
   `correction_semantic.judge_runner.run_daily_judge(run=True, ...)` を呼ぶ
   （`judge_runner.py:259-330`）
4. `run_daily_judge` は `utterances=None` のとき
   `utterance_archive.query.query_utterances_all_projects()` から全 PJ の発話を取得し
   （`judge_runner.py:338-350`）、`_resolve_tracked_slugs()`（`judge_runner.py:110-141`）
   で `fleet-config.json` の `tracked_projects` を pj_slug 集合へ変換し、
   `_apply_population_filters()`（`judge_runner.py:144-186`）で tracked外/cutoff外を
   除外する
5. tracked filter を通った発話は Haiku 判定（`call_haiku` = `safe_llm_call`経由・
   `judge_runner.py:84-94`）にかけられ、`correction_semantic.batch.ingest_judgement_results`
   （`batch.py:188-400`）が `channel=LLM_JUDGE_CHANNEL`（= `"llm_judge"`）の
   `WeakSignal` を `weak_signals.jsonl` へ書く（`batch.py:239,362-368`）
6. `llm_judge` は `REVIEW_CHANNELS`（y/n に出す content-rich チャネル）の一員
   （`correction_semantic/review_channels.py:37-39`）
7. `evolve --drain` の後段フェーズで `daily_review.build_review(pj_slug, ...)`
   （`daily_review.py:381` 以降）と `bootstrap_backlog.build(pj_slug, ...)`
   （`phases_capture.py:225-229`）が、`_pj_slug_match()`（`daily_review.py:242,258`）で
   **cwd から解決した pj_slug に一致する weak_signal のみ**を y/n 候補として提示する

**実測（2026-08-24・本タスクで確認）**: `~/.claude/evolve-anything/fleet-config.json`
の `tracked_projects` に `/Users/matsukaze-takashi/matsukaze-utils/evolve-anything` が
既に含まれている（`cat` で直接確認）。つまり **judge の tracked filter 側は
evolve-anything を素通しする状態が既に整っている**。

### Codex 側（切れているのはステップ1のみ）

`scripts/phase1_codex_probe.py`（Phase 1・マージ済み）はステップ1相当の**読み取り専用**
版だが、`tempfile.mkdtemp()` の使い捨てディレクトリに出力するだけで（ADR-055 本文
「Evidence」節）、**`utterances.db` に一切書かない**。したがってステップ2以降
（judge / weak_signals / y/n）に Codex 発話が到達する経路は**存在しない**。

**切れ目はステップ1だけ**: Codex ログ → `utterances.db` への writer が無いこと。
ステップ2〜7（judge・weak_signals・review_channels・daily_review）は
**発話の出所（CC/Codex）を一切区別しないコード**であることを上記 file:line で確認済み
（`pj_slug` / `session_id` / `text` / `timestamp` のみを見る）。ゆえに縦串の全量は
「ステップ1相当の Codex 版 writer を1つ足す」だけで足りる。

## 最小の縦串の設計

### D1.5-1. Codex 専用 extractor（新規ファイル。既存 `extractor.py` に触れない）

`scripts/lib/utterance_archive/extractor_codex.py`（新規）に
`extract_utterances_codex(jsonl_path, *, pj_slug_filter, stats=None) -> Iterator[Utterance]`
を実装する。`Utterance`（`extractor.py:63-75` の既存 dataclass）をそのまま import して
使う（新しい型を作らない）。

実装する契約（ADR-055 D3/D4/D5a の内容を、Phase 2 の共通 reducer 抽出を待たずに
このファイル内で完結させる。裁定Aと同じ「既存コードは変更しないが、新規コードは
D2 が完成した前提の構造で書く」方針）:

- **D4（子セッション除外）**: ファイル単位。1PJスコープに限定した ADR-055 X2 の
  「Phase1 対象ファイルに限定した参照集合でも判定結果は同一」を踏襲し、
  `pj_slug_filter` を通過したファイル集合内で `sub_agent_activity.agent_thread_id` の
  集合を作り、`session_meta.id` が含まれるファイルを除外する
- **D3（機構マーカー9種除外）**: ADR-055 の表（9種の先頭タグ）をそのまま使う
- **D5a（セグメント帰属 + チャネル制約付き dedup）**: ADR-055 の確定した手順
  （直前 `session_meta` への帰属、`(file, text_hash)` 異チャネル1:1マッチング T=100ms）
  をそのまま実装する
- **pj_slug 判定**: `session_meta.cwd`（または直近 `turn_context` の cwd）に対して
  `pj_slug.resolve_pj_slug(cwd)`（`pj_slug.py:175-221`）を呼ぶ。**`pj_slug_fast` は
  使わない**（後述 D1.5-3 の理由）。ingest はバッチ文脈（hot path でない）なので
  subprocess コストを払ってよい（`resolve_pj_slug` の docstring が明記する「hot-path
  安全性は `pj_slug_fast` の責務」という区分どおり）

**D2 との関係**: このファイルは Phase 2 で `parser_codex` として吸収される前提の
構造（イベント正規化 → reducer 相当の内部関数分離）で書くが、**Phase 2 の共通
reducer を作らず、`Utterance` 化までこのファイル内で完結させる**。Phase 2 で
D2 に着手するときは、このファイルの「イベント正規化」部分だけを `parser_codex` として
切り出せばよい（使い捨てではなく Phase 2 への足場として設計するが、Phase 2 の完了
条件ではない＝No-Go でも消さずに残せる）。

### D1.5-2. Codex ingest（新規ファイル。既存 `ingest.py` に触れない）

`scripts/lib/utterance_archive/ingest_codex.py`（新規）に
`ingest_codex_projects(sessions_root=None, db_path=None, tracked_pj_slugs=None, days=None, progress=False) -> dict`
を実装する。`ingest.py:51-126` の `ingest_pj_dir` と同じ増分パターン（mtime/offset
を `ingest_state` テーブルで突合）を踏襲するが、走査元が `~/.codex/sessions/**/*.jsonl`
である点と、書き込み対象を `tracked_pj_slugs` でフィルタする点が異なる。

- **DB は同じ `utterances.db`・同じ `ingest_state` テーブルを共有する**（新規 DB・
  新規テーブルを作らない。制約6を満たす — 列も増やさない。`_store.insert_utterances`
  をそのまま呼ぶ）
- **`ingest_state.source_path` は Codex ログの絶対パスなので CC 側と衝突しない**
  （物理 PK `(source_path, line_no)` は無傷）
- **論理 UNIQUE `(session_id, timestamp, text_hash)`**: `session_id` は D5a が確定した
  `session_meta.id`（Codex 側 UUID 名前空間）であり、CC 側の `sessionId`（別の UUID
  名前空間）と衝突する確率は実務上ゼロ（両者とも UUID v4/v7 相当）。**衝突が起きた
  場合の挙動**: `ON CONFLICT DO NOTHING`（`store.py:66`）により後着ちが黙って
  スキップされる。これは既存 CC-only 運用でも同じ契約であり、本追補が新たに導入する
  リスクではない
- **`tracked_pj_slugs`**: 呼び出し側（後述 D1.5-3）が `{"evolve-anything"}` を渡す。
  D1.5-1 の `extract_utterances_codex` は `pj_slug_filter` でファイル走査自体を
  絞り込むため、他 PJ のファイルは中身を読みもしない（4層防御の「入口で弾く」層）

### D1.5-3. 呼び出し配線（既存2箇所の CC ingest 呼び出しの隣に追加）

CC 側 ingest は2箇所から呼ばれている（上記「配線図」参照）。**朝の y/n に到達する
経路として本質的なのは `fleet ingest`（daily-run 経由）側**であり、`evolve --drain`
（手動）側は任意実行のため無くても daily-run が拾う。ただし片方だけに足すと
「手動 `evolve --drain` は Codex 発話を拾わないが daily-run は拾う」という非対称が
生まれ、手動実行時の期待値がズレる。**両方に追加する**（CC ingest と同型の
既存パターンを踏襲するだけであり、新しい配線パターンを発明しない）。

1. `scripts/lib/fleet/cli.py:345-354`（`_run_ingest`）に、CC ingest の直後で
   ```python
   from utterance_archive import ingest_codex as _codex_ingest
   codex_res = _codex_ingest.ingest_codex_projects(
       tracked_pj_slugs={"evolve-anything"}, progress=not args.quiet,
   )
   ```
   を追加し、結果を `[fleet:ingest]` ログ行に1行追記する（CC 側の
   `inserted=`/`files=` と同じ形式）。**例外は CC ingest と同様に fail-open せず、
   `fleet ingest` 全体の戻り値には影響させる**（CC 側が duckdb 不在で rc=1 を返す
   のと同型。Codex 側のみの障害は rc に影響させない= try/except で吸収し
   `codex_error` フィールドに記録。CC ingest 自体の失敗は既存どおり rc=1 のまま
   ＝本追補は CC 側の既存契約を変更しない）
2. `skills/evolve/scripts/evolve/phases_capture.py:145-154` の CC ingest 呼び出し
   直後（`try/except` ブロックの隣。既存の `subagent_traces` ingest 呼び出し
   （`phases_capture.py:161-172`）と同じ形の独立 `try/except`）に、同じ
   `ingest_codex_projects(tracked_pj_slugs={"evolve-anything"}, progress=False)`
   呼び出しを追加し `result["utterance_ingest_codex"]` に記録する

**`tracked_pj_slugs={"evolve-anything"}` のハードコード**: Phase 1.5 は「1PJ限定」が
設計の前提（Phase 2 で全PJ化する際に D7 の pj_slug 正規化を確定させる）。この
allowlist は Phase 2 着手時に `fleet-config.json.tracked_projects` 由来の動的集合へ
差し替える（D7 の一部として）。ハードコードである旨と差し替えタイミングをコード
コメントに明記する。

### 図解（変更ファイル一覧）

| ファイル | 変更 |
|---|---|
| `scripts/lib/utterance_archive/extractor_codex.py` | 新規。D3/D4/D5a を実装 |
| `scripts/lib/utterance_archive/ingest_codex.py` | 新規。`utterances.db` への増分書込 |
| `scripts/lib/fleet/cli.py` | `_run_ingest` に Codex ingest 呼び出しを追加（既存関数への数行追記） |
| `skills/evolve/scripts/evolve/phases_capture.py` | CC ingest 直後に Codex ingest 呼び出しを追加（既存関数への数行追記） |
| `scripts/lib/store_registry.py` | `utterances.db` 宣言（`store_registry.py:206-217`）の `writer` 説明文に `ingest_codex.py` を追記（**新規キー追加ではない**。既存宣言のテキスト更新のみ） |
| `docs/decisions/drafts/055-codex-rollout-ingest.md` | Consequences に「Phase 1.5 導入」を1行追記（親 ADR 側の整合） |

**触らないもの（明示）**: `correction_semantic/judge_runner.py` / `batch.py` /
`weak_signals/*` / `review_channels.py` / `daily_review.py` / `extractor.py`（CC 側）/
`ingest.py`（CC 側）/ `store.py` の schema / `fleet-config.json`（既に evolve-anything
を含むため変更不要）。**新設ゼロ**（制約1を満たす）。

## LLM 呼び出しのコスト見積もり（実測ベース）

実測（2026-08-24・`scripts/phase1_codex_probe.py --days 730 --pj-filter evolve-anything`
を実行。read-only・production ストア byte hash 不変を自己検証済み）:

| 項目 | 値 |
|---|---|
| 対象ファイル数（evolve-anything・全履歴） | 326 |
| 生 user 発話 | 1,231 |
| 子セッション除外後 | 884 |
| 機構マーカー除外後 | 608 |
| dedup 後（最終 utterance 数） | **396** |
| 推定バッチ数（batch_size=30） | 14 |
| 推定トークン数（合計） | **289,576** |

**この 396 件は「初回バックフィル」の総量**であり、1回の daily-run で処理される
わけではない。`judge_runner.DEFAULT_DAILY_UTTERANCE_LIMIT=200` /
`DEFAULT_DAILY_TOKEN_LIMIT=150,000`（`judge_runner.py:71-72`）が**既存のまま**
上限として効くため、実際の消費は初回 daily-run で ~200件、翌日以降で残り ~196件、
という形に自然に分割される（**新しい制限機構を作らない**。既存の日次上限がそのまま
Codex 由来発話にも適用される）。

**モデル**: 既定 `model="haiku"`（`judge_runner.py:265,608`）。単価は本文書では
確認していない（**未測定・理由: 実装時点の Anthropic API 料金表を都度参照する
運用のため、設計文書に固定額を書くと陳腐化する**。実装 PR のコミットメッセージまたは
daily-run 実行ログに実測トークン数×その時点の単価で概算を残すことを推奨する）。

**参考（重複度合いの目安）**: `resolve_pj_slug` ベースで evolve-anything に帰属する
Codex セッションファイルは全履歴で 308/748（本タスクで実測）。probe の 326 という
数値は `pj_filter` 文字列一致（cwd 文字列に `"evolve-anything"` を含むか）という
異なる判定方式のため単純比較はできない（**この乖離自体が下記「未実測」節の対象**）。

## Test Plan（変異表）

対象: D1.5-1（extractor_codex）・D1.5-2（ingest_codex）・D1.5-3（配線）。

| # | 分類 | 変異 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|---|
| 1 | ① 要素を消す | `tracked_pj_slugs` フィルタを削除し、全 Codex PJ のログを無条件で ingest する | PJ スコープ隔離（制約7の1PJ限定） | 2つの異なる pj_slug（evolve-anything / 他PJ）を含む fixture で、ingest 後に他PJ行が0件であることの assertion |
| 2 | ② 意味を壊す（表現差） | D3 の機構マーカー判定を、先頭タグの前に半角/全角空白・改行が挟まる表現には対応しない実装のまま残す | D3（機構発話の誤検出防止・表現差クラス） | 先頭タグの前に全角スペースを挿入した fixture で除外されることの assertion |
| 3 | ③ 分散・入替 | D5a のセグメント帰属を「直前の `session_meta`」でなく「ファイル先頭の `session_meta.id`」に差し替える（ADR-055 C-2 変異9と同型だが、共通 reducer でなく `extractor_codex.py` 単体に対して行う） | D5a（発話は自分のセグメントに帰属する） | 複数 `session_meta` を持ち、各セグメントに異なる `session_id` を期待する fixture |
| 4 | ④ 鮮度（検査の無効化に相当） | `ingest_codex_projects` を、`ingest_state` の mtime 更新前の DB スナップショットに対してベンチを走らせる（今回挿入した行を見ずに「前回までの行数」を見る） | C-1 相当（今回の実行が実際に行を生成したこと） | 実行前後の `SELECT COUNT(*)` の差分を直接 assertion する検査（件数の推移でなく差分そのものを見る） |
| **陽性対照** | — | 正常な evolve-anything Codex fixture（機構マーカーなし・単一 `session_meta`・重複なし）を ingest する | — | **緑のままであること**（新規行が期待どおり insert され、既存 CC 側の行数は不変） |

**codex 指摘由来の追加変異（異種の素通り経路）**:

| # | 素通り経路 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|
| 5 | `resolve_pj_slug` を呼ばず `Path(cwd).name`（`phase1_codex_probe.py:452` と同じ実装）に差し替える | 制約7（worktree cwd の正規化。実測: 748ファイル中50件で `pj_slug_fast`/`resolve_pj_slug` の判定が食い違う） | `/wt/ea-*` のような sibling-dir worktree cwd を持つ fixture で、evolve-anything に正しく帰属することの assertion |
| 6 | `store_registry` に新しい `StoreDeclaration`（例: `codex_utterances.db`）を追加してしまう実装に差し替える | 制約1（新設凍結。#379 Step 1） | `pytest -q scripts/lib/tests/test_shrink_freeze.py` が赤くなることを確認（既存テストがそのまま検査経路になる。新規テスト不要） |
| 7 | `insert_utterances` を呼ばず独自の `INSERT INTO utterances_codex ...` を書いてしまう実装に差し替える | 制約6（既存テーブルへの列追加なし・別テーブルを作らない） | `query_utterances_all_projects()`（既存 reader・変更しない）で Codex 由来行が取得できることの assertion（別テーブルだと reader から見えず沈黙する） |
| 8 | dry-run（`ctx.dry_run=True`）でも Codex ingest を実行してしまう実装に差し替える | dry-run 純度（制約3） | `evolve --drain --dry-run` 相当の呼び出しで `utterances.db` の byte hash が実行前後不変であることの assertion（`pitfall_dryrun_stateful_store_write` の再発防止と同型） |

各変異は、適用パッチ・対象テスト名・期待する単独失敗・陽性対照の成功を実装時の
成果物として残す（実装完了報告に実行結果を添える）。

**さらに探索する入力クラス**: 空白/Unicode/改行/エスケープ（変異2で一部カバー）、
巨大入力（1ファイルに session_meta セグメントが100件超あるケース。ADR-055 M4 実測の
最大値は101件/ファイルなのでこれを超える合成 fixture で確認）、実行順序（daily-run
の `fleet ingest` → `judge_runner` の順序が入れ替わっても壊れないか。実行契約上
順序は固定なのでテスト対象は「順序が守られていること」自体）、キャッシュ鮮度
（変異4でカバー）。**未探索のクラスが残る限り完了扱いにしない**（実装時に列挙を
更新する）。

## 副作用チェック（`verify-side-effects.md` / C-3 相当）

- dry-run で `utterances.db` への書込がゼロ（変異8で検証）
- `~/.codex/` 配下への書込がゼロ（read-only で開くことを実装で保証し、
  fixture ディレクトリを read-only mount またはファイル権限で検証する）
- `store_registry` の active key 集合が実装前後で増えていない（変異6で検証）

## No-Go 時の後始末

Phase 1 の使い捨てスクリプトと異なり、**Phase 1.5 は本番 `utterances.db` に実際に
行を書く**（Phase 1 の「一時ディレクトリを消すだけ」という単純な後始末は使えない）。
これは Phase 1 からの明確なコスト増であり、意図的にスコープを1PJに絞っている理由の
一つでもある。

**戻せるもの**:
- `utterances.db` の Codex 由来行: `DELETE FROM utterances WHERE source_path LIKE '<home>/.codex/sessions/%'`
  （物理 PK に含まれる `source_path` で判別可能。D5b (c) の prefix 判定と同じ規約）
- 対応する `ingest_state` 行: 同じ `source_path` prefix で削除
- **まだ人間 y/n を通っていない** `weak_signals.jsonl` / `correction_idioms.jsonl` /
  `correction_judged.jsonl` の Codex 由来エントリ: `provenance.source_path`
  （`batch.py:339`）の prefix で判別してフィルタ除去

**戻せないもの（明示）**:
- **既に人間が y/n で承認し `corrections.jsonl` / skill diff へ昇格した項目**。
  これは Codex 由来かどうかに関わらず「人間が承認した成果物」であり、他の受理済み
  correction と同じ扱いでロールバック対象**外**とする（`evolve-revert` の対象範囲
  ＝「evolve drain 経由の新規採用のみ」という既存契約と同型）。No-Go 判定が
  「Codex ログからの入力を今後止める」という意味であっても、既に生まれた改善を
  取り消す理由にはならない
- Haiku 呼び出し済みのトークン消費（課金は不可逆）

**後始末の実行**: 削除用スクリプトは実装 PR の一部として用意する（本文書は設計の
みで実装しない）。削除は `git`管理下のコード変更ではなく DATA_DIR 側のデータ操作
のため、テストでは「削除前後で対象外PJ（既存CC発話）の行数・内容が不変であること」
を必ず assertion する（副作用チェックと同型）。

## 計測（先延ばししない。3問1行ずつ）

### 前提1: 「Codex ログ由来の weak_signal が実際に y/n に出て、人間が y/n した結果は
　妥当か」（Go/No-Go 判定に相当する定性的な質を、Phase 1.5 稼働後に確認する必要がある）

- ①今日、自分の権限内で生成できないか: **No**。Phase 1.5 の実装自体が未着手のため、
  実際の y/n 提示は生成できない。具体手順: 本文書の実装 PR をマージし `fleet ingest`
  を1回手動実行すれば当日中に生成できるが、それは「今日」ではなく「実装完了後」
  なので延期に該当する
- ②片側だけでも今出る結論はないか: **Yes**。`phase1_codex_probe.py` の実行結果
  （396件・14バッチ・約29万トークン）は「Codex ログから抽出できる発話の量は
  ゼロではない」という片側の結論を今日出している（本文書のコスト見積もり節）
- ③手元の既存データで代理測定できないか: **一部Yes**。ADR-055 の M10（CC 側
  ベースライン指摘率 4.48%）を Codex 側の期待陽性数の参考値として使える
  （ADR-055 X6 が既に「期待陽性は約12件」と算出済み）。ただし Codex 特有の
  発話パターン（コード生成依頼が多い等）が同じ指摘率になる保証はない代理測定
  に留まる

**延期する実行契約**: 起点=本設計の実装 PR マージ日 / 再測条件=Phase 1.5 稼働後、
`fleet ingest` の Codex 由来 `inserted` 累計が ADR-055 X6 の分母（259件相当）に
達した時点 / 実行者=オーケストレーター（頭）/ 判定者=ユーザー / 期限=ADR-055 本文の
既存契約と統一し **2026-09-30**（ADR-055「再測の実行契約」表と同一期限を流用し、
二重の期限を作らない）/ 期限超過時=ADR-055 の既存契約どおり「Codex 実利用量が
想定に達しなかった」と結論し Phase 2 を見送る

### 前提2: 「`resolve_pj_slug` は M6 の 28.9% 孤立問題を実際に改善するか」

- ①今日、自分の権限内で生成できないか: **Yes（実施済み）**。本文書作成中に実測した
  （上記「配線図」節および「コスト見積もり」節の実測値）。`resolve_pj_slug` と
  `pj_slug_fast` を実際の Codex ログ748ファイルの cwd に対して両方実行し比較した
- ②③: 該当なし（①で解決済み）

**実測結果（2026-08-24）**: 748ファイルの先頭行 cwd に対し `pj_slug_fast` と
`resolve_pj_slug` を比較。**50/748（6.7%）で判定が食い違い、全て `resolve_pj_slug`
側が worktree cwd を本体 repo 名へ正しく畳んでいた**（例:
`/Users/matsukaze-takashi/wt/ea-533` → `pj_slug_fast`="ea-533" /
`resolve_pj_slug`="evolve-anything"）。`pj_slug_fast` で `wt-` パターンを含む
orphan 相当は 198/748（26.5%）で、ADR-055 M6 の 28.9%（675/677ファイル対象）と
近い水準。**この実測は M6 問題を全PJ規模で解決する Phase 2/D7 の代替にはならない**
（本文書は evolve-anything 1PJ限定のみ検証した）が、「ingest 時に `resolve_pj_slug`
を使えば少なくとも1PJ分は正しく畳める」という Phase 1.5 の前提を裏付ける。

### 前提3: 「probe（`phase1_codex_probe.py`）の 326 と `resolve_pj_slug` ベースの 308
　の乖離の原因」

- ①今日、自分の権限内で生成できないか: **No**。原因特定には両方の走査ロジックを
  突合するデバッグが必要で、本文書のスコープ外（設計文書であり実装ではない）
- ②片側だけでも今出る結論はないか: **Yes**。乖離は判定方式の違い（`pj_filter`
  文字列一致 vs `resolve_pj_slug` の git 解決）に起因することは特定済みで、
  どちらも「概ね300前後」という同じ桁のオーダーであることは確認済み
- ③代理測定: 該当なし

**延期する実行契約**: 起点=本文書のコミット日 / 再測条件=D1.5-1
（`extractor_codex.py`）の実装完了時、実装が使う実際の pj_slug 判定ロジック
（`resolve_pj_slug`）でファイル数を数え直し、probe の 326 との差分を実装 PR の
Test Plan 実行結果に記録する / 実行者=実装担当 / 判定者=実装レビュアー（Codex
系列レビュー） / 期限=D1.5-1 の実装 PR マージまで / 期限超過時=該当なし
（実装 PR 自体がこの期限内に完了する前提のため、超過は実装未着手と同義）

## Consequences

- **ADR-055 本文の「Phase 1/2 の間、ユーザー体験は一切変わらない」は evolve-anything
  1PJ について訂正される**。Phase 1.5 稼働後は、evolve-anything で `evolve --drain`
  または daily-run を実行すると、Codex ログ由来の weak_signal が y/n 候補に混ざり
  うる。他 PJ のユーザー体験は不変（allowlist スコープ）
- Go/No-Go 判定（ADR-055 裁定B）は Phase 1.5 の稼働と独立している。Phase 1.5 は
  「配線が繋がるか」を検証するものであり「Codex ログの指摘の質が良いか」を判定
  するものではない。質の低い提案が y/n に混ざっても、人間 y/n という既存の安全弁
  （制約4）がそのまま機能する
- No-Go（Phase 2 を見送る判断が下った）場合でも、**Phase 1.5 で既に人間が承認した
  correction は取り消さない**（上記「No-Go 時の後始末」参照）。これは「Phase 1.5 は
  実質的に取り消せない一方向の変化を生みうる」ことを意味し、1PJ限定というスコープの
  狭さがこのリスクを抑える主な手段になっている
- `tracked_pj_slugs={"evolve-anything"}` のハードコードは Phase 2 で D7 の動的解決に
  置き換える必要がある技術的負債として残る（コード内に明記する）
- Phase 1.5 の daily-run 稼働により、ADR-055 の再測分母（259件相当）が自動蓄積される
  副次効果がある（上記「計測」節）。これは意図した設計であり偶然ではない
