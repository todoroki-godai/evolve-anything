# reclassify Specification

## Purpose
TBD - created by archiving change backfill-noise-filter. Update Purpose after archive.
## Requirements
### Requirement: extract サブコマンドで "other" intent のプロンプトを抽出できなければならない（MUST）
`python3 reclassify.py extract --project <name>` で、sessions.jsonl から "other" intent を持つプロンプトを JSON 形式で stdout に出力しなければならない（MUST）。

#### Scenario: 基本的な抽出
- **WHEN** `extract --project my-project` を実行する
- **THEN** `user_intents` に "other" を持つプロンプトが JSON 配列で出力される
- **AND** 各要素は `{"session_id": str, "intent_index": int, "prompt": str}` 形式である

### Requirement: extract に --include-reclassified オプションを提供しなければならない（MUST）
`extract --include-reclassified` を指定した場合、`reclassified_intents` が存在するセッションでも残 "other" を抽出しなければならない（MUST）。

#### Scenario: 既分類セッションからの残 "other" 抽出
- **GIVEN** session "sess-001" に `reclassified_intents: ["debug", "other", "implementation"]` がある
- **WHEN** `extract --include-reclassified` を実行する
- **THEN** `reclassified_intents` の値を参照し、index 1 の "other" が抽出対象に含まれる

#### Scenario: --include-reclassified で reclassified_intents が存在しない場合
- **GIVEN** session "sess-002" に `reclassified_intents` が存在しない
- **WHEN** `extract --include-reclassified` を実行する
- **THEN** `user_intents` を参照し、"other" を抽出する（従来動作と同じ）

#### Scenario: フラグなしの既存動作維持
- **GIVEN** session "sess-001" に `reclassified_intents` が存在する
- **WHEN** `extract`（フラグなし）を実行する
- **THEN** session "sess-001" はスキップされる（既存動作を維持）

### Requirement: apply サブコマンドで再分類結果を書き戻せなければならない（MUST）
`python3 reclassify.py apply --input <result.json>` で、分類結果を sessions.jsonl の `reclassified_intents` に書き戻さなければならない（MUST）。

#### Scenario: 分類結果の書き戻し
- **GIVEN** `result.json` に `[{"session_id": "sess-001", "intent_index": 1, "category": "debug"}]` がある
- **WHEN** `apply --input result.json` を実行する
- **THEN** sess-001 の `reclassified_intents[1]` が "debug" に更新される

### Requirement: SKILL.md Step 2 で Claude Code ネイティブ LLM による分類を実行しなければならない（MUST）
分類は SKILL.md Step 2 で Claude Code セッション内の LLM が実行する。subprocess（`claude -p --model haiku`）を使用してはならない（MUST NOT）。

#### Scenario: SKILL.md Step 2 による分類フロー
- **WHEN** `/rl-anything:backfill` の Step 2 が実行される
- **THEN** `reclassify.py extract --include-reclassified` で "other" プロンプトを抽出する
- **AND** Claude Code が SKILL.md に記載されたカテゴリ定義に基づき各プロンプトを分類する
- **AND** 分類結果を JSON ファイルに書き出す
- **AND** `reclassify.py apply --input <result.json>` で書き戻す

### Requirement: auto サブコマンドを提供してはならない（MUST NOT）
`reclassify.py` に `auto` サブコマンドは存在してはならない（MUST NOT）。関連する `_build_classify_prompt()`、`_call_claude_classify()`、`auto_reclassify()` も削除しなければならない（MUST）。

#### Scenario: auto サブコマンドの不在
- **WHEN** `python3 reclassify.py auto` を実行する
- **THEN** エラーとなる（サブコマンドが存在しない）

### Requirement: VALID_CATEGORIES に skill-invocation を含めなければならない（MUST）
`reclassify.py` の `VALID_CATEGORIES` に `skill-invocation` を追加し、Claude Code の分類結果として受け入れ可能にしなければならない（MUST）。`common.PROMPT_CATEGORIES` にはキーワードとして追加しない（タグ構造で決定的に判定できるため）。

#### Scenario: skill-invocation カテゴリの受け入れ
- **WHEN** apply で `{"category": "skill-invocation"}` を含む分類結果を書き戻す
- **THEN** 有効なカテゴリとして受け入れられる（invalid_categories にカウントされない）

### Requirement: 無効なカテゴリはカウントしてスキップしなければならない（MUST）
apply で `VALID_CATEGORIES` に含まれないカテゴリが指定された場合、`invalid_categories` としてカウントし、該当エントリをスキップしなければならない（MUST）。

#### Scenario: 無効なカテゴリのスキップ
- **WHEN** apply で `{"category": "unknown-category"}` を含む分類結果を書き戻す
- **THEN** 該当エントリはスキップされ、`invalid_categories` が 1 増加する

