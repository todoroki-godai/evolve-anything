## ADDED Requirements

### Requirement: Evolve SHALL run /simplify after remediation when Python files changed
evolve の Compile ステージで remediation がファイルを変更した場合、Python ファイルの変更を含む場合のみ `/simplify` を実行する。

#### Scenario: Remediation changes Python files
- **WHEN** remediation の `record_outcome()` で記録された `fix_detail.changed_files` を集約し、`.py` ファイルが1つ以上含まれる
- **THEN** `/simplify` を実行し、コード品質チェックを行う
- **AND** `/simplify` の結果（git diff）をユーザーに提示し、AskUserQuestion で「適用」「元に戻す」を選択させる

#### Scenario: Remediation changes only Markdown files
- **WHEN** remediation の `fix_detail.changed_files` を集約した結果、すべて Markdown（.md）である
- **THEN** `/simplify` をスキップする

#### Scenario: Remediation makes no changes
- **WHEN** remediation が0件のファイルを変更した（全スキップ or dry-run）、または `fix_detail.changed_files` が空
- **THEN** `/simplify` をスキップする

### Requirement: /simplify gate SHALL be backward compatible
`/simplify` が利用できない環境（古い Claude Code バージョン）でも evolve パイプラインは正常に動作する。

#### Scenario: /simplify unavailable
- **WHEN** Claude Code のバージョンが v2.1.63 未満、または `/simplify` スキルが利用不可
- **THEN** `/simplify` ゲートをスキップし、従来通り regression gate のみで品質チェックを行う
- **AND** レポートに「/simplify: スキップ（未対応バージョン）」と表示する

#### Scenario: /simplify available
- **WHEN** Claude Code のバージョンが v2.1.63 以上で `/simplify` が利用可能
- **THEN** 条件を満たす場合に `/simplify` を実行する

### Requirement: /simplify results SHALL be included in evolve report
`/simplify` を実行した場合、その結果を evolve の最終レポートに含める。

#### Scenario: /simplify executed and applied
- **WHEN** `/simplify` が実行され、ユーザーが変更を適用した
- **THEN** レポートの Compile セクションに「/simplify: N件の改善を適用」と表示する

#### Scenario: /simplify executed but reverted
- **WHEN** `/simplify` が実行され、ユーザーが変更を元に戻した
- **THEN** レポートの Compile セクションに「/simplify: 実行済み・変更なし」と表示する
