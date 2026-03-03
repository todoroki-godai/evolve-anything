## MODIFIED Requirements

### Requirement: extract サブコマンドで "other" intent のプロンプトを抽出できなければならない（MUST）
`python3 reclassify.py extract --project <name>` で、sessions.jsonl から "other" intent を持つプロンプトを JSON 形式で stdout に出力しなければならない（MUST）。corrections.jsonl のデータがある場合、correction が紐付いたセッションの intent を優先的に抽出対象とする。

#### Scenario: 基本的な抽出
- **WHEN** `extract --project my-project` を実行する
- **THEN** `user_intents` に "other" を持つプロンプトが JSON 配列で出力される
- **AND** 各要素は `{"session_id": str, "intent_index": int, "prompt": str}` 形式である

#### Scenario: correction 紐付きセッションの優先抽出
- **WHEN** `extract --project my-project` を実行し、corrections.jsonl にセッション sess-001 のレコードが存在する
- **THEN** sess-001 の "other" intent が結果の先頭に含まれる

#### Scenario: corrections.jsonl との session_id ベース join
- **WHEN** `extract --project my-project` を実行する
- **THEN** corrections.jsonl の各レコードの `session_id` を sessions.jsonl の `session_id` と内部結合（inner join）し、correction が紐付くセッション一覧を取得する
- **AND** join キーは `session_id`（文字列完全一致）とする
- **AND** corrections.jsonl が存在しない場合は空集合として扱い、通常の "other" intent 抽出のみ行う

## ADDED Requirements

### Requirement: correction confidence を intent 分類の補助シグナルに使用しなければならない（MUST）
reclassify の LLM 分類時に、corrections.jsonl から該当セッションの correction 情報を context として提供しなければならない（MUST）。

#### Scenario: correction 情報付き分類
- **WHEN** セッション sess-001 に correction `{"correction_type": "iya", "last_skill": "evolve"}` が紐付いている
- **THEN** LLM への分類プロンプトに「ユーザーは evolve スキルに対して修正を行った」という context が追加される
