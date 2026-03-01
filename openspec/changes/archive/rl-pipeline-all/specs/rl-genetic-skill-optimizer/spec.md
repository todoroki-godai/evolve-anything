## ADDED Requirements

### Requirement: 遺伝的最適化エンジン
LLM を用いてスキルファイルのバリエーションを生成し、適応度関数で評価して最良のバリエーションを選択する遺伝的最適化フレームワークを提供する。

#### Scenario: dry-run 実行
- **WHEN** `--dry-run --generations 3 --population 3` で実行する
- **THEN** 3世代×3集団の構造テストが完了し、各世代のスコア（ダミー値）が表示される

#### Scenario: 世代ループ
- **WHEN** `--generations N --population M` で実行する
- **THEN** N 世代にわたり、各世代 M 個体の評価→選択→次世代生成が行われる

#### Scenario: エリート選択
- **WHEN** 次世代を生成する
- **THEN** 前世代の最高スコア個体がそのまま次世代に引き継がれる（エリート選択）

### Requirement: バックアップと復元
最適化対象のスキルファイルを破壊しないよう、バックアップ機構を提供する。

#### Scenario: バックアップ作成
- **WHEN** 最適化を開始する
- **THEN** 元のスキルファイルが `.backup` サフィックスでバックアップされる

#### Scenario: バックアップ二重作成防止
- **WHEN** バックアップが既に存在する状態で最適化を開始する
- **THEN** 既存バックアップを上書きしない

#### Scenario: 復元
- **WHEN** `--restore` オプションで実行する
- **THEN** バックアップからスキルファイルを復元し、バックアップファイルを削除する

### Requirement: 突然変異と交叉
LLM を用いてスキルのバリエーションを生成する。

#### Scenario: 突然変異
- **WHEN** 個体に突然変異を適用する
- **THEN** `claude -p` で改善指示を与え、変異後のスキル内容を取得する

#### Scenario: 交叉
- **WHEN** 2つの親個体から交叉を適用する
- **THEN** `claude -p` で両方の良い点を組み合わせた子個体を生成する

#### Scenario: LLM 呼び出し失敗時
- **WHEN** `claude` CLI の呼び出しがタイムアウトまたは失敗する
- **THEN** 元の個体をそのまま返す（フォールバック）

### Requirement: カスタム適応度関数
プロジェクト固有の適応度関数を `scripts/rl/fitness/` から読み込んで使用できる。

#### Scenario: カスタム関数実行
- **WHEN** `--fitness {name}` で実行する
- **THEN** `scripts/rl/fitness/{name}.py` が呼び出され、スコアが返る

#### Scenario: デフォルト評価
- **WHEN** `--fitness default` で実行する
- **THEN** LLM ベースのスキル品質評価でスコアを算出する

### Requirement: 世代データ保存
各世代の個体データと最終結果を JSON で保存する。

#### Scenario: 世代保存
- **WHEN** 1世代の評価が完了する
- **THEN** `generations/{run_id}/gen_{n}/` に各個体の JSON が保存される

#### Scenario: 結果保存
- **WHEN** 全世代の最適化が完了する
- **THEN** `generations/{run_id}/result.json` に最良個体とスコア推移が保存される
