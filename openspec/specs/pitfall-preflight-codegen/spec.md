## ADDED Requirements

### Requirement: Preflight script template proposal
Pre-flight対応=Yes の Active pitfall に対して、Root-cause カテゴリに応じた検証スクリプトテンプレートを提案する（SHALL）。

#### Scenario: Action category pitfall
- **WHEN** Active pitfall の Root-cause カテゴリが `action` で Pre-flight対応=Yes である
- **THEN** `skills/evolve/templates/preflight/action.sh` ベースのテンプレートパスを提案する

#### Scenario: Tool_use category pitfall
- **WHEN** Active pitfall の Root-cause カテゴリが `tool_use` で Pre-flight対応=Yes である
- **THEN** `skills/evolve/templates/preflight/tool_use.sh` ベースのテンプレートパスを提案する

#### Scenario: Unknown category fallback
- **WHEN** Root-cause カテゴリがテンプレートマッピングに存在しない
- **THEN** `skills/evolve/templates/preflight/generic.sh` をフォールバックとして提案する

### Requirement: Script template content
テンプレートにはチェック対象の説明プレースホルダ、成功/失敗の判定ロジック、exit code の規約を含める（SHALL）。

#### Scenario: Template placeholder structure
- **WHEN** テンプレートが生成された
- **THEN** 以下の構造を含む: `# TODO: {pitfall_title} の検証ロジックを実装` プレースホルダ、`if` 条件分岐、`exit 0`（成功）/ `exit 1`（失敗）の終了コード規約

### Requirement: Codegen proposals in hygiene report
pitfall_hygiene() の結果に `codegen_proposals` フィールドを追加する（SHALL）。

#### Scenario: Proposals included in report
- **WHEN** Pre-flight対応=Yes の Active pitfall が存在する
- **THEN** `codegen_proposals: [{pitfall_title, category, template_path}]` が hygiene 結果に含まれる

#### Scenario: No preflight pitfalls
- **WHEN** Pre-flight対応=Yes の Active pitfall が存在しない
- **THEN** `codegen_proposals` は空リストとなる
