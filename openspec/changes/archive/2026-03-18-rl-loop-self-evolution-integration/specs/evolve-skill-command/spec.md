## ADDED Requirements

### Requirement: /rl-anything:evolve-skill コマンド

`/rl-anything:evolve-skill <name>` コマンドを提供しなければならない（MUST）。指定スキルに対して自己進化適性判定→テンプレート組み込み→人間確認を1コマンドで実行する。

#### Scenario: スキル名指定で実行
- **WHEN** `/rl-anything:evolve-skill my-skill` を実行する
- **THEN** `.claude/skills/my-skill/` を対象として適性判定を実行し、結果を表示する

#### Scenario: ファイルパス指定で実行
- **WHEN** `/rl-anything:evolve-skill .claude/skills/my-skill/SKILL.md` を実行する
- **THEN** 指定パスからスキルディレクトリを解決し、適性判定を実行する

#### Scenario: 引数なしで実行
- **WHEN** `/rl-anything:evolve-skill` を引数なしで実行する
- **THEN** 対象スキルの指定を求めるメッセージを表示する

### Requirement: 適性判定結果の表示

適性判定結果として5軸スコア（実行頻度・失敗多様性・出力評価可能性・外部依存度・判断複雑さ）、合計スコア、適性レベル、アンチパターン検出結果を表示しなければならない（MUST）。

#### Scenario: 適性 high のスキル
- **WHEN** 対象スキルの適性が high と判定される
- **THEN** 5軸スコアと「変換を推奨」メッセージを表示し、組み込みの承認を求める

#### Scenario: 適性 medium のスキル
- **WHEN** 対象スキルの適性が medium と判定される
- **THEN** 5軸スコアと「変換可能 — ユーザー判断に委ねます」メッセージを表示し、組み込みの承認を求める

#### Scenario: 適性 low のスキル
- **WHEN** 対象スキルの適性が low と判定される
- **THEN** 5軸スコアと「変換非推奨」メッセージを表示し、組み込みを行わない

#### Scenario: アンチパターン検出
- **WHEN** アンチパターンが2件以上検出される
- **THEN** 各パターン名と理由を表示し、「変換非推奨」として組み込みを行わない

#### Scenario: 既に自己進化済み
- **WHEN** 対象スキルが既に自己進化パターンを持っている
- **THEN** 「既に自己進化対応済みです」とメッセージ表示し、終了する

### Requirement: パターン組み込みの実行と確認

承認後、`apply_evolve_proposal()` を呼び出して SKILL.md へのセクション追加と `references/pitfalls.md` 作成を実行し、結果を表示しなければならない（MUST）。

#### Scenario: 組み込み成功
- **WHEN** ユーザーが組み込みを承認する
- **THEN** SKILL.md にセクションが追加され、`references/pitfalls.md` が作成され、変更内容のサマリーが表示される

#### Scenario: 組み込み却下
- **WHEN** ユーザーが組み込みを却下する
- **THEN** ファイルに変更を加えず、終了する

#### Scenario: バックアップ作成
- **WHEN** 組み込みが実行される
- **THEN** SKILL.md 変更前に `.md.pre-evolve-backup` が作成される
- **AND** バックアップパスが結果サマリーに含まれる

### Requirement: --dry-run オプション

`/rl-anything:evolve-skill my-skill --dry-run` で適性判定結果のみ表示し、ファイル変更を行わないモードを提供しなければならない（MUST）。

#### Scenario: dry-run 実行
- **WHEN** `--dry-run` オプション付きで実行する
- **THEN** 適性判定結果と組み込み予定内容を表示するが、ファイルへの変更は行わない
