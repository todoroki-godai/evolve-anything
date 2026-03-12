## MODIFIED Requirements

### Requirement: discover 統合
`run_discover()` の結果に `tool_usage_patterns` キーとしてツール利用分析結果を含める（MUST）。
`--tool-usage` フラグで有効化する（MUST）。デフォルトは無効。
結果の `installed_artifacts` に mitigation_metrics を含める（MUST）。

#### Scenario: フラグ有効時の結果統合
- **WHEN** `run_discover(tool_usage=True)` が呼ばれる
- **THEN** 結果辞書に `tool_usage_patterns` キーが含まれ、`builtin_replaceable`、`repeating_patterns` が格納される
- **AND** `installed_artifacts` の各エントリに `recommendation_id` を持つものは `mitigation_metrics` を含む

#### Scenario: フラグ無効時のスキップ
- **WHEN** `run_discover()` がデフォルト引数で呼ばれる
- **THEN** `tool_usage_patterns` キーは結果に含まれない

#### Scenario: セッションファイルが存在しない場合
- **WHEN** 対象プロジェクトのセッションディレクトリが存在しない
- **THEN** 空の結果を返し、エラーを発生させない（MUST）

### Requirement: evolve 統合
evolve の discover フェーズ表示にツール利用分析セクションを含める（MUST）。
対策状態に応じて表示を切り替える（MUST）。

#### Scenario: evolve 経由での自動有効化
- **WHEN** evolve が discover を呼び出す
- **THEN** `tool_usage=True` で呼び出す（MUST）。discover 単体のデフォルト（無効）とは独立

#### Scenario: 対策済み推奨の表示
- **WHEN** installed_artifacts に mitigation_metrics を持つエントリがあり mitigated が True
- **THEN** 「対策済み (artifacts) — 直近 N 件検出」形式で表示する

#### Scenario: 未対策推奨の表示
- **WHEN** recommended_artifacts に recommendation_id 付きエントリがある（= 未導入）
- **THEN** 従来通り件数と改善提案を表示する

### Requirement: 閾値定数化
Step 10.2 の閾値をハードコードから `tool_usage_analyzer.py` のモジュール定数に移行する（MUST）。

#### Scenario: 定数定義
- **WHEN** `tool_usage_analyzer` モジュールがロードされた時点で
- **THEN** `BUILTIN_THRESHOLD=10`、`SLEEP_THRESHOLD=20`、`BASH_RATIO_THRESHOLD=0.40` が定義されている

#### Scenario: SKILL.md での参照
- **WHEN** Step 10.2 が閾値を使用する
- **THEN** `tool_usage_analyzer.py` の定数名を参照して記述する（ハードコード値ではなく）
