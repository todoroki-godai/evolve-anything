## ADDED Requirements

### Requirement: 統合スコア算出
技術品質、ドメイン品質、構造品質の3軸でスキルを評価し、重み付き統合スコアを返す。

#### Scenario: 3軸統合スコア
- **WHEN** スキルファイルを評価する
- **THEN** technical (40%) + domain (40%) + structure (20%) の統合スコアを JSON 形式で返す

#### Scenario: ドメイン自動判定
- **WHEN** CLAUDE.md からドメインを推定する
- **THEN** ゲーム/API/Bot/ドキュメント/汎用 を自動判定し、ドメイン品質の評価軸を切り替える

### Requirement: 改善提案
スコアリング結果に基づき、具体的かつ実行可能な改善提案を含める。

#### Scenario: 改善提案の具体性
- **WHEN** スコアが 1.0 未満の観点がある
- **THEN** 該当観点に対する具体的な改善提案を improvements フィールドに含める

### Requirement: JSON 出力フォーマット
評価結果は構造化された JSON で出力する。

#### Scenario: 出力フォーマット
- **WHEN** 評価が完了する
- **THEN** `target`, `timestamp`, `scores`（各軸の内訳）, `integrated_score`, `summary`, `improvements` を含む JSON を返す
