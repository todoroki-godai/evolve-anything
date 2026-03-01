## ADDED Requirements

### Requirement: 適応度関数のインターフェース
すべての適応度関数は共通インターフェースに従う。

#### Scenario: stdin/stdout インターフェース
- **WHEN** `python3 fitness_func.py` を実行し、stdin にスキル内容を渡す
- **THEN** stdout に数値（0.0〜1.0）を出力する

### Requirement: 組み込み適応度関数
Plugin に2つの汎用適応度関数を組み込む。

#### Scenario: default 関数
- **WHEN** `--fitness default` で実行する
- **THEN** LLM ベースの汎用評価（明確性・完全性・構造・実用性）でスコアを算出する

#### Scenario: skill_quality 関数
- **WHEN** `--fitness skill_quality` で実行する
- **THEN** ルールベースの構造品質チェックでスコアを算出する

### Requirement: カスタム適応度関数の配置
プロジェクト固有の適応度関数を `scripts/rl/fitness/{name}.py` に配置して利用可能にする。

#### Scenario: カスタム関数の検索順序
- **WHEN** `--fitness {name}` で実行する
- **THEN** 以下の順序で検索する: (1) プロジェクトの `scripts/rl/fitness/{name}.py` → (2) Plugin 内の `scripts/fitness/{name}.py`

#### Scenario: スコア範囲
- **WHEN** 任意のスキル内容を評価する
- **THEN** 0.0〜1.0 の範囲のスコアを stdout に出力する
