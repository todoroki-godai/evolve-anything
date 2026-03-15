## ADDED Requirements

### Requirement: context:fork for heavy-output skills
evolve, audit, discover スキルの SKILL.md frontmatter に `context: fork` を追加し、大量出力がメインコンテキストを汚染しないようにする（MUST）。fork コンテキストでは会話履歴にアクセスできないため、ファイルシステムベースで動作するスキルのみが対象。fork されたエージェントの最終メッセージは親コンテキストに返される。詳細結果は `<DATA_DIR>/<skill>-report.json` にファイル出力する（MUST）。fork コンテキスト内では AskUserQuestion を使用してはならない（MUST NOT）。ユーザー承認が必要な操作は fork 復帰後にメインコンテキストで実施する（MUST）。

#### Scenario: evolve with context:fork
- **WHEN** ユーザーが `/rl-anything:evolve` を実行する
- **THEN** evolve は fork されたサブエージェントコンテキストで実行される
- **AND** 詳細結果は `<DATA_DIR>/evolve-report.json` にファイル出力される
- **AND** 最終メッセージとして要約がメインコンテキストに返される

#### Scenario: audit with context:fork
- **WHEN** ユーザーが `/rl-anything:audit` を実行する
- **THEN** audit は fork されたコンテキストで実行され、レポートは `<DATA_DIR>/audit-report.json` にファイル出力される

#### Scenario: discover with context:fork
- **WHEN** ユーザーが `/rl-anything:discover` を実行する
- **THEN** discover は fork されたコンテキストで実行され、候補リストは `<DATA_DIR>/discover-report.json` にファイル出力される

#### Scenario: fork context does not use AskUserQuestion
- **WHEN** evolve の fork コンテキスト内で remediation が auto_fixable でない issue を検出する
- **THEN** 提案を結果ファイルに出力し、fork 復帰後にメインコンテキストでユーザー承認を求める

### Requirement: ${CLAUDE_SKILL_DIR} variable usage
SKILL.md 内でスキルローカルのファイル（templates/, scripts/ 等）を参照する際、`${CLAUDE_SKILL_DIR}` 変数を使用しなければならない（MUST）。ハードコードされた相対パスを排除する。

#### Scenario: evolve template reference
- **WHEN** evolve SKILL.md がテンプレートファイルを参照する
- **THEN** `${CLAUDE_SKILL_DIR}/templates/` の形式で参照され、ハードコードパスは使用されない

#### Scenario: plugin root vs skill dir distinction
- **WHEN** スキルがプラグイン全体のリソース（scripts/lib/ 等）を参照する
- **THEN** `${CLAUDE_PLUGIN_ROOT}` を使用し、スキルローカルのリソースには `${CLAUDE_SKILL_DIR}` を使用する

### Requirement: Agent model specification in skills
スキルが Agent tool でサブエージェントを起動する際、用途に応じたモデルを明示的に指定する指針を SKILL.md に記載しなければならない（MUST）。

#### Scenario: discover uses haiku for pattern detection
- **WHEN** discover がパターン検出のためにサブエージェントを起動する
- **THEN** Agent tool の `model` パラメータに `haiku` を指定する

#### Scenario: default model inheritance
- **WHEN** スキルがモデル指定なしでサブエージェントを起動する
- **THEN** 親コンテキストのモデルが継承される（inherit 動作）

### Requirement: Skill-level hooks for evolve
evolve スキルの frontmatter に PostToolUse skill hook を定義し、remediation 後のリグレッション検出を自動化しなければならない（MUST）。

#### Scenario: regression check after bash execution
- **WHEN** evolve 実行中に Bash ツールが使用される
- **THEN** PostToolUse hook が regression_gate.py --quick-check を実行する
- **AND** stdin から PostToolUse イベント JSON を受け取り、Bash command から変更対象 .py ファイルを推定する
- **AND** 対象ファイルに `py_compile.compile()` で構文チェックを実行する
- **AND** exit code 0（正常）/ 1（エラー）を返し、stderr に JSON result を出力する

#### Scenario: hook cleanup after skill completion
- **WHEN** evolve スキルの実行が完了する
- **THEN** skill hook は自動的にクリーンアップされ、後続の操作に影響しない
