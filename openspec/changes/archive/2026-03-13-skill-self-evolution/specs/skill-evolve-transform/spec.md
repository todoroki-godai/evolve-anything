## ADDED Requirements

### Requirement: Self-evolution pattern insertion
evolve_skill_proposal() は適性判定で「高」または「中（ユーザー承認済み）」のスキルに対して、自己進化パターンを組み込む変換提案を生成する（SHALL）。

挿入するセクション:
1. **Pre-flight Check**: `references/pitfalls.md` を読み Active+Pre-flight対応=Yes の項目を適用する指示
2. **自己更新ルール**: 更新対象と判断基準のテーブル
3. **Failure-triggered Learning**: エラー/リトライ/ユーザー訂正/再発の4トリガーテーブル
4. **Pitfall Lifecycle Management**: Candidate→New→Active→Graduated→Pruned の5段階ルール
5. **成功パターン枠**: `## Success Patterns` セクション（1-2件記録用）
6. **根本原因カテゴリ指示**: 記録時に memory/planning/action/tool_use/context_loss を付与する指示

#### Scenario: Full transformation proposal
- **WHEN** 適性高（13点）のスキルに対して変換提案を生成する
- **THEN** 上記6セクションを含む SKILL.md の差分と、空の references/pitfalls.md テンプレートを提案として出力する

#### Scenario: User approval required
- **WHEN** 変換提案が生成された
- **THEN** AskUserQuestion で「適用する」「スキップ」を選択させる。承認後にのみファイルを変更する

### Requirement: Template-based customization
変換提案は `templates/` のテンプレートをベースに、LLM がスキルの文脈に合わせてカスタマイズする（SHALL）。

#### Scenario: Customized pre-flight for deploy skill
- **WHEN** デプロイ系スキルに変換を適用する
- **THEN** Pre-flight Check に「デプロイ前の確認」という文脈に合った見出しが使われる

#### Scenario: Customized triggers for evaluation skill
- **WHEN** 評価系スキルに変換を適用する
- **THEN** Failure-triggered Learning のトリガーに「評価結果の乖離」が追加される

### Requirement: pitfalls.md template creation
変換時に `references/pitfalls.md` を空テンプレートで作成する（SHALL）。テンプレートには構造化フィールドの定義を含む。

#### Scenario: Empty pitfalls template
- **WHEN** 変換が適用された
- **THEN** `references/pitfalls.md` が以下の構造で作成される:
  - `## Active Pitfalls`（Hot/Warm 層）
  - `## Candidate Pitfalls`（Cold 層、品質ゲート通過前）
  - `## Graduated Pitfalls`（Cold 層、ワークフロー統合済み）
  - 各項目テンプレート: Status/Last-seen/Root-cause/Pre-flight対応/Avoidance-count

### Requirement: Success patterns section
変換時に `## Success Patterns` セクションを SKILL.md に追加する（SHALL）。成功パターンを1-2件記録するための枠を設ける。

#### Scenario: Success pattern recorded
- **WHEN** スキル実行が成功し、特に効果的だったアプローチがあった
- **THEN** Success Patterns セクションに短い説明と日付が記録される

#### Scenario: Success pattern limit
- **WHEN** Success Patterns が既に2件記録されている
- **THEN** 最も古いパターンを置き換える（最新2件を維持）

### Requirement: Root cause category assignment
Failure-triggered Learning の指示に、根本原因カテゴリの付与を含める（SHALL）。

カテゴリ:
- `memory`: コンテキスト消失、前の情報の忘却
- `planning`: 手順の誤り、依存関係の見落とし
- `action`: コマンドミス、パラメータ誤り
- `tool_use`: ツール選択ミス、API仕様の誤解
- `context_loss`: 圧縮による情報消失

#### Scenario: Error categorized
- **WHEN** デプロイエラーが pitfall として記録される
- **THEN** `Root-cause: action — CDK deploy コマンドのパラメータ不足` のように分類される

#### Scenario: Cross-skill pattern detection enabled
- **WHEN** 複数のスキルで `tool_use` カテゴリの pitfall が蓄積された
- **THEN** evolve の Report で「tool_use カテゴリの問題がN件のスキルに分散 — 共通ルール化を検討」と表示できる

### Requirement: Template file missing handling
テンプレートファイル（`skills/evolve/templates/self-evolve-sections.md` または `skills/evolve/templates/pitfalls.md`）が存在しない場合、変換を中止しエラーを報告する（SHALL）。

#### Scenario: Template file not found
- **WHEN** `skills/evolve/templates/self-evolve-sections.md` が存在しない状態で変換が実行された
- **THEN** 変換を中止し「テンプレートファイルが見つかりません: skills/evolve/templates/self-evolve-sections.md」とエラーを表示する

#### Scenario: Partial template missing
- **WHEN** `self-evolve-sections.md` は存在するが `pitfalls.md` テンプレートが存在しない
- **THEN** 変換を中止し、欠落しているテンプレートファイル名を報告する

### Requirement: Invalid LLM customization handling
LLM がテンプレートをカスタマイズした結果が不正なマークダウン構造の場合、フォールバック処理を行う（SHALL）。

#### Scenario: LLM generates malformed markdown
- **WHEN** LLM のカスタマイズ結果に必須セクション（Pre-flight Check, Failure-triggered Learning）が欠落している
- **THEN** カスタマイズを破棄し、テンプレートをそのまま挿入する。ユーザーに「LLM カスタマイズに失敗したため、テンプレートをそのまま適用しました」と通知する

#### Scenario: LLM adds unexpected sections
- **WHEN** LLM のカスタマイズ結果にテンプレートで定義されていないセクションが追加された
- **THEN** 追加セクションを除去し、テンプレートで定義されたセクションのみを適用する
