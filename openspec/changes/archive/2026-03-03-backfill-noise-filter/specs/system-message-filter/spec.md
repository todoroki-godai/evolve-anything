## ADDED Requirements

### Requirement: 中断シグナルを user_prompts / user_intents から除外しなければならない（MUST）
`[Request interrupted` で始まるメッセージは、`user_prompts` および `user_intents` に記録してはならない（MUST NOT）。

#### Scenario: 中断シグナルの除外
- **WHEN** human メッセージの content が `[Request interrupted by user]` である
- **THEN** `user_prompts` に追加されない
- **AND** `user_intents` に追加されない
- **AND** サマリの `filtered_messages` が 1 増加する

#### Scenario: 中断シグナルのバリエーション
- **WHEN** human メッセージの content が `[Request interrupted by user for tool use]` である
- **THEN** 同様に除外される（prefix マッチ）

### Requirement: ローカルコマンド出力を user_prompts / user_intents から除外しなければならない（MUST）
`<local-command-` を含むメッセージは、`user_prompts` および `user_intents` に記録してはならない（MUST NOT）。

#### Scenario: local-command-stdout の除外
- **WHEN** human メッセージの content が `<local-command-stdout>some output</local-command-stdout>` である
- **THEN** `user_prompts` に追加されない
- **AND** `user_intents` に追加されない

#### Scenario: local-command-caveat の除外
- **WHEN** human メッセージの content が `<local-command-caveat>Caveat: ...</local-command-caveat>` である
- **THEN** 同様に除外される

### Requirement: タスク通知を user_prompts / user_intents から除外しなければならない（MUST）
`<task-notification>` を含むメッセージは、`user_prompts` および `user_intents` に記録してはならない（MUST NOT）。

#### Scenario: タスク通知の除外
- **WHEN** human メッセージの content が `<task-notification>` で始まる
- **THEN** `user_prompts` に追加されない
- **AND** `user_intents` に追加されない

### Requirement: コマンドタグからスキル名を抽出して記録しなければならない（MUST）
`<command-name>` タグを含むメッセージは、コマンド名を抽出し、`user_intents` に `skill-invocation` として、`user_prompts` にコマンド名を記録しなければならない（MUST）。

#### Scenario: スラッシュコマンドの抽出
- **WHEN** human メッセージの content に `<command-name>/commit</command-name>` が含まれる
- **THEN** `user_intents` に `skill-invocation` が追加される
- **AND** `user_prompts` に `/commit` が追加される

#### Scenario: プラグインコマンドの抽出
- **WHEN** human メッセージの content に `<command-name>/rl-anything:backfill</command-name>` が含まれる
- **THEN** `user_intents` に `skill-invocation` が追加される
- **AND** `user_prompts` に `/rl-anything:backfill` が追加される

#### Scenario: command-name タグのパース失敗
- **WHEN** `<command-name>` タグは存在するがコマンド名を抽出できない
- **THEN** メッセージ全体を除外する（`user_prompts` / `user_intents` に記録しない）

### Requirement: フィルタされたメッセージ数をサマリに含めなければならない（MUST）
バックフィルサマリの JSON に `filtered_messages` フィールドを含め、フィルタで除外したメッセージの総数を報告しなければならない（MUST）。

#### Scenario: フィルタ数のサマリ出力
- **WHEN** 10 件のシステムメッセージがフィルタされた
- **THEN** サマリ JSON に `"filtered_messages": 10` が含まれる

### Requirement: 通常のユーザープロンプトはフィルタしてはならない（MUST NOT）
上記のパターンに該当しないメッセージは、従来どおり `user_prompts` / `user_intents` に記録しなければならない（MUST）。フィルタの false positive は許容しない。

#### Scenario: 通常プロンプトの通過
- **WHEN** human メッセージの content が `Fix the login bug` である
- **THEN** `user_prompts` に `Fix the login bug` が追加される
- **AND** `user_intents` に `classify_prompt()` の結果が追加される

#### Scenario: 角括弧で始まるが中断シグナルではないプロンプト
- **WHEN** human メッセージの content が `[重要] この関数を修正して` である
- **THEN** フィルタされず、通常のプロンプトとして記録される

### Requirement: content がリスト形式の場合も最初の text ブロックにフィルタを適用しなければならない（MUST）
human メッセージの content がリスト形式（text ブロックの配列）の場合、最初の text ブロックに対してシステムメッセージフィルタを適用しなければならない（MUST）。

#### Scenario: リスト形式の content に中断シグナルが含まれる場合
- **WHEN** human メッセージの content が `[{"type": "text", "text": "[Request interrupted by user]"}]` である
- **THEN** 最初の text ブロックに対してフィルタが適用される
- **AND** `user_prompts` に追加されない
- **AND** `user_intents` に追加されない

#### Scenario: リスト形式の content にコマンドタグが含まれる場合
- **WHEN** human メッセージの content が `[{"type": "text", "text": "<command-name>/commit</command-name>..."}]` である
- **THEN** 最初の text ブロックからコマンド名が抽出される
- **AND** `user_intents` に `skill-invocation` が追加される
- **AND** `user_prompts` にコマンド名が追加される
