## ADDED Requirements

### Requirement: 全 SKILL.md に YAML frontmatter を追加
rl-anything プラグインの全スキル SKILL.md ファイルに YAML frontmatter を追加しなければならない (MUST)。
frontmatter には `name`, `description` を必須とする (SHALL)。

`disable-model-invocation` の設定基準は以下の通りとしなければならない (MUST):
- `disable-model-invocation: true`（破壊的・副作用のあるスキル）: evolve, prune, update, backfill
- `disable-model-invocation: false` または省略（情報表示・読み取り専用スキル）: discover, audit, feedback, version, evolve-fitness

対象スキル（frontmatter 未設定の9件）:
- evolve, prune, discover, audit, feedback, backfill, update, version, evolve-fitness

#### Scenario: frontmatter 付きスキルの自動起動
- **WHEN** ユーザーが「スキルの品質を改善したい」と発言
- **THEN** Claude が `evolve` スキルの description にマッチし、自動起動を検討できなければならない (SHALL)

#### Scenario: 破壊的スキルのユーザー明示呼び出し
- **WHEN** `prune` スキルに `disable-model-invocation: true` が設定されている
- **THEN** Claude が自動起動せず、ユーザーが `/rl-anything:prune` で明示的に呼び出さなければならない (MUST)

#### Scenario: 情報表示スキルの自動起動許可
- **WHEN** `version` スキルに `disable-model-invocation` が未設定または `false` である
- **THEN** Claude がコンテキストに応じて自動起動を判断できる (MAY)

### Requirement: description は英語で記述しトリガーワードを日英併記
description フィールドは英語で記述しなければならない (MUST)。
末尾に `Trigger:` セクションを設け、日本語と英語のトリガーワードを併記しなければならない (SHALL)。

#### Scenario: 英語 description の例
- **WHEN** `evolve` スキルの frontmatter を確認する
- **THEN** description が英語で記述され、Trigger セクションに日英のトリガーワードが含まれなければならない (MUST)

### Requirement: 既に frontmatter があるスキルは変更しない
`genetic-prompt-optimizer`, `rl-loop-orchestrator`, `generate-fitness` は既に適切な frontmatter を持つため、既存の frontmatter を維持しなければならない (MUST)。

#### Scenario: 既存 frontmatter の保持
- **WHEN** `genetic-prompt-optimizer/SKILL.md` の frontmatter を確認する
- **THEN** 既存の frontmatter が変更されていてはならない (MUST NOT)

### Requirement: openspec-* SKILL.md の description にトリガーワードを補強
`openspec-apply-change`, `openspec-explore`, `openspec-verify-change`, `openspec-archive-change`, `openspec-propose` の SKILL.md の description に旧 commands のトリガーワードを追加しなければならない (MUST)。
これにより commands 削除後も同等の発見性を維持する。

#### Scenario: opsx:apply の代替
- **WHEN** ユーザーが「opsx apply」「openspec apply」「タスク実装」と発言
- **THEN** `openspec-apply-change` スキルの description がマッチし、発見可能でなければならない (SHALL)
