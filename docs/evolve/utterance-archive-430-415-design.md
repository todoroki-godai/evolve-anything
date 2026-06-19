# Design: utterance アーカイブ + jsonl-first ingest 一本化（#430 + #415）

Status: draft v2（second-opinion レビュー反映済み、ユーザーレビュー待ち）
Date: 2026-06-10
Issues: #430（utterance アーカイブ）, #415（sessions.db 再肥大）
Related: #12（temporal validity + provenance）, #434（ストア契約ゲート — **PR #435 でマージ済み、依存解消**）, ADR-042（store dir resolver）

## 背景（実測）

| 事実 | 数値 |
|------|------|
| transcript の寿命 | `cleanupPeriodDays` により削除（14→60 に暫定延長済み 2026-06-10） |
| 全PJ human 発話 | 6,967 件 / user 行 47,349（直近約2週間分、約15%が人間） |
| テキスト量 | 10.4MB/28日（うち 9.5MB は 2000字超の長文ペースト）。年換算 136MB |
| sessions.db bloat | 9.6GB / 実データ14MB（約680倍）。原因は hook per-fire の connect→INSERT→close |

correction 個人辞書（#431）・暗黙シグナル（#432）・outcome 帰属（#433 correction 軸）・遡及分析は
すべて human 発話の履歴が基盤。現状は毎日失われており、再構築不能。

## Goals

1. 全PJ human 発話＋最小文脈を**恒久アーカイブ**に batch ingest する（#430）
2. sessions.db の再肥大を**書き込みパターンの変更で根治**する（#415）
3. 両者を**同一の batch ingest 経路**に一本化し、hot path（hooks）から DuckDB 接続を消す

## Non-goals

- 発話の意味解析・LLM 処理（#431 の領分。本工事はゼロ LLM）
- assistant 側発話の全文保存（最小文脈＝直前 assistant の tool 名要約のみ）
- 汎用メモリエンジン導入（claude-mem/supermemory 非採用の既決事項どおり）
- リアルタイム性（batch で十分。リアルタイム検出は既存 hot hook の領分）

## 設計

### 原則: hooks は jsonl にのみ書く。DuckDB に書くのは batch ingest だけ

```
[hot path]  hooks → sessions.jsonl に追記のみ（fsync 不要、行追記）
[cold path] batch ingest（audit/evolve 実行時 + evolve-fleet コマンド）
            ├─ sessions.jsonl → sessions.db に取り込み → jsonl rotate
            ├─ ~/.claude/projects/*/*.jsonl → utterances.db に増分取り込み
            └─ 保険 compaction: file_size vs rows×平均行長の乖離が閾値超で rebuild
```

### Phase A（PR1, #415）: session_store の jsonl-first 化

- `session_store.append()` から DuckDB 経路を削除し、`_append_jsonl()` を正に昇格
  （現実装 `scripts/lib/session_store.py:51-89` の per-fire connect→INSERT→close が病巣）
- `ingest()` を新設: jsonl → db を最上位 1 connection で取り込み（DuckDB checkpoint pitfall 準拠）。
  **冪等性**: db 側の重複除去キーは既存 `migrate_from_jsonl` と同じ `(session_id, timestamp)`。
  rotate（`.ingested-<ts>` リネーム）は **db への取り込み成功を確認した後**に行い、
  rotate 済みファイルは glob パターンで ingest 対象から恒久除外（mtime に依存しない）。1世代保持
- 読み取り系（`count_unique_since` / `query`）は **union read に書き換える**:
  db の結果と未 ingest jsonl の結果を `(session_id, timestamp)` で dedup して合算する。
  ⚠ 現実装は db / jsonl の**排他分岐**（`if HAS_DUCKDB and SESSIONS_DB.exists()` で db があれば
  jsonl を見ない）なので「既存 fallback 再利用」では済まない。両関数の書き換え＋テスト追加が
  PR1 の主工数（second-opinion 指摘 — 当初見積もりより Phase A は大きい）。
  union read が必要な理由: trigger_engine 等は **ingest と非同期**（セッションイベント時）に
  count を読むため、「ingest 直後にしか読まない」仮定は成立しない
- 保険 compaction: ingest 完走時にサイズ乖離 >10倍 で CREATE TABLE AS → swap の rebuild
- 呼び出し元（trigger_engine 等）は SessionStore API 経由なので無変更

### Phase B（PR2, #430）: utterance アーカイブ

**ストア**: `utterances.db`（新規 DuckDB、ADR-042 resolver 経由の DATA_DIR 直下）。
writer は batch ingest のみ＝ hot path ゼロなのでロック競合・肥大が構造的に起きない。
**Phase A の union read は不要**（未 ingest 中間データが存在しないため。sessions と utterances で
読み取り契約が異なることに注意 — second-opinion 指摘の非対称性）。
#434 契約 registry へ宣言する（registry は現在 jsonl のみ対象なので、`.db` ストア対応の
スキーマ拡張を本 PR に含める — PR #435 の備考に記載済みの宿題）。

**スキーマ**（temporal validity + provenance を最初から、#12 接続）:

```sql
CREATE TABLE utterances (
    source_path      TEXT NOT NULL,   -- provenance: 由来 transcript の絶対パス（resolve 済み）
    line_no          INTEGER NOT NULL,-- 由来ファイル内の行番号（物理的に安定な一意性）
    pj_slug          TEXT NOT NULL,   -- ADR-031 準拠の worktree 安全 slug
    session_id       TEXT NOT NULL,
    timestamp        TEXT NOT NULL,   -- 発話時刻（transcript 由来）
    text             TEXT NOT NULL,
    text_hash        TEXT NOT NULL,   -- 重複除去用（sha256 先頭16桁）
    prev_action      TEXT,            -- 直前 assistant の tool 名列（定義は下記）
    source_kind      TEXT NOT NULL,   -- 'dialogue' | 'long_paste' | 'excluded_pj'
    extractor_version INTEGER NOT NULL,
    ingested_at      TEXT NOT NULL,
    PRIMARY KEY (source_path, line_no)
);
-- 論理重複の防止: resume された session の transcript は履歴 replay で
-- 同一発話が別ファイルに複製されるため、物理 PK だけでは弾けない。
CREATE UNIQUE INDEX uq_utt_logical ON utterances(session_id, timestamp, text_hash);
CREATE INDEX idx_utt_session ON utterances(session_id, timestamp);
CREATE INDEX idx_utt_pj_time ON utterances(pj_slug, timestamp);

CREATE TABLE ingest_state (          -- 増分取り込みの既処理管理
    source_path  TEXT PRIMARY KEY,
    mtime        DOUBLE NOT NULL,
    line_offset  INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);
```

**キー設計の判断**（second-opinion の核心指摘への回答）:
- ~~PRIMARY KEY (session_id, turn_index)~~ は**不採用**。CC の resume/compaction で同一
  session_id の transcript が複数ファイルに分かれ、turn 番号が再カウントされる場合に
  KEY violation かサイレント重複が起きる
- 物理 PK = `(source_path, line_no)`（どのファイルの何行目か — 増分 ingest と自然に整合）
- 論理重複（resume の履歴 replay による同一発話の複製）は
  `UNIQUE (session_id, timestamp, text_hash)` + `INSERT OR IGNORE` 相当で弾く
- `turn_index` カラムは**持たない**。下流（#431）は `(session_id, timestamp)` 順で引く。
  「human 発話の通し番号」が必要なら query 側で ROW_NUMBER() を使う（定義のズレを保存しない）
- transcript の source_path は `~/.claude/projects/` 配下（PJ の worktree とは無関係の
  home 配下絶対パス）なので、worktree 由来のパス不安定性は**発生しない**。
  `Path.resolve()` で正規化のみ行う

**prev_action の定義**（実装依存の揺れを封じる）:
「当該 human 発話より前で、直前の human 発話より後にある assistant メッセージ群の
tool_use 名を出現順に重複除去せず join、上限 10 個 + 超過時 `…`」。
並行 tool_use は transcript の記録順。assistant メッセージが無ければ NULL。

**抽出ロジック**（corpus-scan で検証済みのものを製品化）:
- human 発話のみ: `isMeta` / `toolUseResult` / `tool_result` content を除外
- harness 注入除外: `<system-reminder` / `<command-name` / `<local-command` / `Caveat:` /
  `[Request interrupted` / `This session is being continued`（learning_trajectory_mining_machinery_turns 準拠）
- 長文（>2000 字）は `source_kind='long_paste'` でタグ保存（除外でなく分類 —
  後から判断を変えられる。検出器 FP 学習: 値でなく文脈で落とす）
- 非対話 PJ（bots の文字起こし等）は `source_kind='excluded_pj'` タグ。デフォルト全 PJ 取り込み

**query API の契約**（second-opinion の blind spot 指摘への回答）:
- `query_utterances(pj_slug, since=None, ...)` — **pj_slug は必須引数**。全PJ共通 DATA_DIR
  単一ファイル pitfall の再発防止（read 側照合の強制）。横断検索は別関数
  `query_utterances_all_projects()` を明示的に呼ぶ（fleet recall 用）
- **source_kind のデフォルトは `('dialogue',)`**。long_paste / excluded_pj を含めるには
  明示的に opt-in する。下流（#431 個人辞書）が分母汚染しない契約を API デフォルトで保証

**増分 ingest**: `ingest_state` の (mtime, line_offset) と突合し、新規/追記分のみ parse。
mtime 同一かつ offset 既達ならスキップ。全量再走査は初回 backfill のみ。
論理 UNIQUE index があるため、万一 state が壊れて再走査しても重複は入らない（冪等）。

**実行タイミング**:
1. audit / evolve 実行時に自動（既存 batch 文脈に同居）
2. `bin/evolve-fleet ingest` 手動実行
3. SessionStart で staleness チェックのみ（observe-first pre-flight、marker 読みで 0.1 秒以下）。
   **marker は ingest 完走時に ingest 自身が書く**（`last_ingest_at` ファイル）。
   **marker 不在 = 「未 ingest」と解釈して advisory を出す**（0日でなく ∞ 扱い —
   second-opinion 指摘の null-safe 誤実装を仕様で封じる）。閾値は最終 ingest > 14日
4. ⚠ advisory は強制ではない（install ≠ enforcement 学習）。実効性の本線は 1 の
   「audit/evolve に同居」であり、3 は安全弁。audit を 14 日以上回さない運用が常態化する場合は
   cleanupPeriodDays=60 が最後の防波堤（60日以内に1回 ingest できれば取りこぼさない）

**retention**: 恒久（#434 registry に `retention: permanent` を宣言）。
cleanupPeriodDays=60 は initial backfill が回るまでの保険として維持。

### Phase C（PR3, 任意）: 検索面

`bin/evolve-fleet recall` の検索対象に utterances.db を追加（既存 keyword 決定論検索の流儀）。

**PR2 の「検索可能」の完了定義**（second-opinion 指摘の曖昧さを解消）:
PR2 done = `query_utterances()` が存在し、pytest の実機 E2E で「14日より古い発話が
session_id / keyword で取得できる」assertion が通ること。fleet コマンドからの操作性は PR3。

## ベンチ（transcript-store-bench ルール準拠）

- 実装前に規模実測: `find ~/.claude/projects -name '*.jsonl' | wc -l` と `du -sh`
  （2026-06-10 時点: 1,127 files / 980MB。PR1 着手時に再取得）
- 実機 1 PJ E2E を pytest に必須化: evolve-anything PJ の実 transcript で ingest を完走させ、
  wall time（< 60s/PJ）/ DB size / row 数を assertion
- backfill script は `--max-files N` サンプリング + phase timeout + 毎件 flush print（暴走判定: CPU100% + 出力0行30秒で kill）

## Success Criteria（issue 逐条）

- [#415] migrate-data 後の sessions.db が batch ingest 運用で乖離 10 倍未満を維持（compaction 発火テスト含む）
- [#415] union read: 未 ingest jsonl にしかないセッションが count/query に反映される回帰テスト
- [#430] 実機 1 PJ E2E で human 発話のみ ingest、wall time / DB size / row 数 assertion
- [#430] 14日より古い発話がアーカイブから検索可能（PR2 完了定義どおり query 関数 + E2E assertion）
- [#430] 機構ターン混入率が目視サンプルで 5% 未満（ベンチ出力にサンプル 20 件を含め目視確認可能に）
- [#430] resume された session（同 session_id 複数ファイル）の fixture で重複ゼロ・KEY violation ゼロ

## リスク・判断

| 論点 | 判断 | 理由 |
|------|------|------|
| utterances を sessions.db に同居 vs 別 DB | **別 DB** | ライフサイクル（compaction/retention）が異なる。writer が batch のみなのでロック設計も独立 |
| 発話原文の保存 | **保存する** | ローカルのみ・chmod 600。年136MB（実測）で容量問題なし。要約保存だと #431 個人辞書の学習材料にならない |
| PK 設計 | **(source_path, line_no) + 論理 UNIQUE** | (session_id, turn_index) は resume/compaction で不安定（second-opinion 指摘採用） |
| DATA_DIR 解決 | `rl_common.store_paths`（ADR-042 resolver）経由 | hook/tool 分裂 pitfall の再発防止 |
| 削除済み過去分 | 諦める | 35日超の生存 9 件のみ。backfill は現存分から |

## second-opinion レビュー（2026-06-10）の指摘と対応

| 指摘 | 対応 |
|------|------|
| (session_id, turn_index) PK は resume/compaction で不安定 | **採用** — 物理 PK + 論理 UNIQUE に変更、turn_index カラム廃止 |
| union read は既存排他分岐の書き換えで Phase A が膨らむ | **採用** — PR1 主工数として明記、回帰テストを Success Criteria に追加 |
| 二重参照は Phase A のみ必要（非対称性） | **採用** — Phase B に明記 |
| staleness marker の更新者・不在時挙動が未定義 | **採用** — ingest 自身が書く / 不在=∞ 扱いを仕様化 |
| rotate 後の再 ingest guard | **採用** — rotate は取り込み成功後 + glob 恒久除外 + 論理 UNIQUE で冪等 |
| source_path の worktree 不安定性 | **非該当** — transcript は home 配下絶対パスで worktree 無関係（resolve のみ） |
| #434 registry 依存 | **解消済み** — PR #435 マージ済み。`.db` 対応拡張を PR2 に含める |
| source_kind の下流フィルタ契約 | **採用** — query API デフォルト `dialogue` のみ、opt-in 方式 |
| prev_action の定義揺れ | **採用** — 範囲・順序・上限を仕様で固定 |
| pj_slug 照合の API 強制（blind spot） | **採用** — pj_slug 必須引数 + 横断は別関数 |
