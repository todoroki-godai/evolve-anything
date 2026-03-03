## Spec: backfill.py user_prompts 記録

### Requirement: user_prompts を session_meta に含めなければならない（MUST）
`parse_transcript()` が `session_meta` に `user_prompts: List[str]` を含めなければならない（MUST）。
`user_prompts[i]` は `user_intents[i]` と同じユーザーメッセージのプロンプト原文であり、`MAX_PROMPT_LENGTH`（500文字）で切り詰める。

#### Scenario: プロンプト原文の記録
- **GIVEN** トランスクリプトにユーザーメッセージ "Fix the login bug" が存在する
- **WHEN** `parse_transcript()` を実行する
- **THEN** `session_meta["user_prompts"]` に "Fix the login bug" が含まれる
- **AND** `user_prompts` のインデックスは対応する `user_intents` と一致する

#### Scenario: 長いプロンプトの切り詰め
- **GIVEN** ユーザーメッセージが 500 文字を超える
- **WHEN** `parse_transcript()` を実行する
- **THEN** `user_prompts` の該当要素は先頭 500 文字に切り詰められる

### Requirement: 既存の user_intents の挙動を変更してはならない（MUST NOT）
`user_prompts` 追加により、既存の `user_intents` の分類ロジック・出力形式・順序が変更されてはならない（MUST NOT）。

#### Scenario: user_intents の互換性維持
- **GIVEN** `user_prompts` 記録機能が有効である
- **WHEN** 既存のトランスクリプトを処理する
- **THEN** `user_intents` の値は `user_prompts` 機能導入前と同一である

---

## Spec: reclassify.py auto サブコマンド

### Requirement: auto サブコマンドで一括分類を実行できなければならない（MUST）
`python3 reclassify.py auto --project <name>` で、"other" intent の抽出・LLM 分類・書き戻しを一括実行できなければならない（MUST）。

#### Scenario: 基本的な自動分類フロー
- **GIVEN** sessions.jsonl に project "my-app" の "other" intent が 3 件存在する
- **WHEN** `python3 reclassify.py auto --project my-app` を実行する
- **THEN** LLM が 3 件を分類し、結果が sessions.jsonl に書き戻される
- **AND** サマリ JSON が stdout に出力される

### Requirement: "other" プロンプトがない場合は LLM を呼び出してはならない（MUST NOT）
"other" intent が 0 件の場合、LLM 呼び出しを行わず正常終了しなければならない（MUST）。

#### Scenario: 対象プロンプトなし
- **GIVEN** sessions.jsonl に "other" intent が存在しない
- **WHEN** `auto` サブコマンドを実行する
- **THEN** LLM 呼び出しは発生しない
- **AND** `{"total_others": 0, "reclassified": 0, ...}` が出力される

### Requirement: haiku モデルを使用しなければならない（MUST）
分類には `claude -p --model haiku` を使用しなければならない（MUST）。haiku 以外のモデルをデフォルトで使用してはならない（MUST NOT）。

#### Scenario: モデル指定
- **GIVEN** `auto` サブコマンドが LLM を呼び出す
- **WHEN** subprocess コマンドが構築される
- **THEN** コマンドに `--model haiku` が含まれる

### Requirement: 分類結果を sessions.jsonl に書き戻さなければならない（MUST）
LLM の分類結果を `apply_reclassification()` 経由で sessions.jsonl の `reclassified_intents` フィールドに書き戻さなければならない（MUST）。

#### Scenario: 書き戻し
- **GIVEN** LLM が session "sess-001" の intent[0] を "debug" と分類した
- **WHEN** `apply_reclassification()` が実行される
- **THEN** sessions.jsonl の該当レコードに `reclassified_intents` が追加され、index 0 が "debug" になる
- **AND** 元の `user_intents` は変更されない

### Requirement: サマリを JSON で stdout に出力しなければならない（MUST）
処理完了後、`{"total_others": N, "reclassified": N, "updated_sessions": N, "updated_intents": N}` 形式の JSON を stdout に出力しなければならない（MUST）。

#### Scenario: サマリ出力
- **GIVEN** "other" が 10 件あり、LLM が 8 件を再分類した
- **WHEN** 処理が完了する
- **THEN** `{"total_others": 10, "reclassified": 8, "updated_sessions": ..., "updated_intents": 8}` が出力される

### Requirement: --dry-run で LLM 呼び出しと apply をスキップしなければならない（MUST）
`--dry-run` オプション指定時、LLM 呼び出しと `apply_reclassification()` をスキップし、対象プロンプト数のみ表示しなければならない（MUST）。

#### Scenario: dry-run モード
- **GIVEN** sessions.jsonl に "other" intent が 5 件存在する
- **WHEN** `auto --dry-run` を実行する
- **THEN** LLM 呼び出しは発生しない
- **AND** sessions.jsonl は変更されない
- **AND** `{"total_others": 5, "reclassified": 0, ...}` が出力される

### Requirement: 無効なカテゴリは "other" のまま維持しなければならない（MUST）
LLM が `VALID_CATEGORIES` に含まれないカテゴリを返した場合、該当 intent は "other" のまま維持しなければならない（MUST）。再分類結果には含めない。

#### Scenario: 無効カテゴリの除外
- **GIVEN** LLM が `{"index": 1, "category": "unknown-category"}` を返した
- **WHEN** 分類結果を処理する
- **THEN** 該当 intent は再分類されず "other" のまま維持される
- **AND** `reclassified` カウントには含まれない

### Requirement: LLM 呼び出し失敗時は該当バッチをスキップしなければならない（MUST）
`claude` CLI のタイムアウト・非ゼロ終了・不正な JSON レスポンスが発生した場合、該当バッチをスキップし、残りのバッチの処理を継続しなければならない（MUST）。エラーは stderr に出力する（SHOULD）。

#### Scenario: claude CLI タイムアウト
- **GIVEN** バッチ 1/3 の LLM 呼び出しが 120 秒以内に応答しない
- **WHEN** タイムアウトが発生する
- **THEN** バッチ 1 はスキップされる
- **AND** バッチ 2, 3 の処理は継続される
- **AND** stderr にタイムアウトのエラーメッセージが出力される

#### Scenario: claude CLI 非ゼロ終了
- **GIVEN** `claude` CLI が returncode != 0 で終了した
- **WHEN** レスポンスを処理する
- **THEN** 該当バッチはスキップされる
- **AND** 残りのバッチ処理は継続される

#### Scenario: 不正な JSON レスポンス
- **GIVEN** LLM レスポンスに JSON 配列が含まれない
- **WHEN** レスポンスをパースする
- **THEN** 該当バッチはスキップされる
- **AND** stderr にエラーメッセージが出力される

### Requirement: バッチサイズは最大 50 件としなければならない（MUST）
LLM への入力はバッチ分割し、1 バッチあたり最大 50 件としなければならない（MUST）。

#### Scenario: バッチ分割
- **GIVEN** "other" intent が 120 件ある
- **WHEN** LLM 分類を実行する
- **THEN** 3 バッチ（50, 50, 20 件）に分割して LLM が呼び出される

---

## Spec: SKILL.md Step 2

### Requirement: auto サブコマンドによる自動化手順を記載しなければならない（MUST）
`reclassify.py auto` を使った自動実行手順を SKILL.md の Step 2 に記載しなければならない（MUST）。

#### Scenario: Step 2 の自動化手順
- **GIVEN** ユーザーが SKILL.md の Step 2 を参照する
- **WHEN** 手順に従って実行する
- **THEN** `reclassify.py auto --project <name>` で一括分類が実行できる

### Requirement: 手動フローも代替手順として残さなければならない（MUST）
手動の extract → 目視分類 → apply フローも代替手順として記載を残さなければならない（MUST）。

#### Scenario: 手動フローの参照
- **GIVEN** ユーザーが LLM を使わずに分類したい
- **WHEN** SKILL.md の手動手順を参照する
- **THEN** extract → 手動分類 → apply の手順が記載されている
