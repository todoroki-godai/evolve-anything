## ADDED Requirements

### Requirement: RECOMMENDED_ARTIFACTS 拡張
既存の `RECOMMENDED_ARTIFACTS` に `recommendation_id` と `content_patterns` フィールドを追加し、Step 10.2 の推奨アクションとの紐付けを宣言的に定義する（MUST）。

#### Scenario: 拡張フィールドの構造
- **WHEN** RECOMMENDED_ARTIFACTS のエントリに `recommendation_id` が設定されている
- **THEN** そのエントリは `recommendation_id`（str: Step 10.2 の条件名）と `content_patterns`（list[str]: hook ファイル内の正規表現パターン）を持つ

#### Scenario: recommendation_id を持つエントリ
- **WHEN** モジュールがロードされた時点で
- **THEN** `builtin_replaceable` と `sleep_polling` の recommendation_id を持つエントリが登録されている

#### Scenario: recommendation_id を持たないエントリ
- **WHEN** エントリに `recommendation_id` が設定されていない
- **THEN** そのエントリは従来通りの導入/未導入チェックのみ行い、mitigation_metrics は付与しない

### Requirement: 対策存在チェック（check_artifact_installed）
`check_hook_installed()` を汎用化した `check_artifact_installed()` を提供し、任意の推奨 artifact の導入状態を検出する（MUST）。

#### Scenario: hook ファイルの存在チェック
- **WHEN** artifact に `hook_path` が指定されている
- **THEN** そのファイルの存在を確認する

#### Scenario: rule ファイルの存在チェック
- **WHEN** artifact に `path` が指定されている（None でない）
- **THEN** そのファイルの存在を確認する

#### Scenario: content_pattern チェック
- **WHEN** artifact に `content_patterns` が指定されており、対応する hook ファイルが存在する
- **THEN** hook ファイル内容に全 content_pattern が含まれるかを正規表現で確認する
- **AND** 全パターンマッチなら `content_matched=True`、不一致なら `content_matched=False` を返す

#### Scenario: ファイル I/O エラー
- **WHEN** hook/rule ファイルの読み取りで OSError が発生した
- **THEN** `installed=False, content_matched=None` を返し、例外を送出しない（MUST）

#### Scenario: content_patterns 未指定
- **WHEN** artifact に `content_patterns` が設定されていない
- **THEN** ファイル存在チェックのみ行い、`content_matched=None` を返す

### Requirement: 条件別メトリクス
対策が存在する推奨について、条件別メトリクスを返す（MUST）。統一遵守率は使用しない。

#### Scenario: builtin_replaceable の検出件数
- **WHEN** builtin_replaceable の対策が存在する
- **THEN** `mitigated=True` と `recent_count`（builtin_replaceable の合計件数）を返す

#### Scenario: sleep_polling の検出件数
- **WHEN** sleep_polling の対策が存在する
- **THEN** `mitigated=True` と `recent_count`（sleep を含む repeating_patterns の合計件数）を返す

#### Scenario: bash_ratio
- **WHEN** bash_ratio を算出する
- **THEN** `bash_calls / total_tool_calls` の比率をそのまま返す（遵守率変換しない）

#### Scenario: テレメトリデータがない場合
- **WHEN** tool_usage 分析結果が空またはテレメトリキーが存在しない
- **THEN** `mitigated` は対策存在チェック結果を維持し、`recent_count=0` を返す（MUST）

### Requirement: evolve レポート表示切替
evolve Step 10.2 で対策済み/未対策に応じて表示を切り替える（MUST）。

#### Scenario: 対策済みの推奨
- **WHEN** installed_artifacts に recommendation_id を持つエントリがあり、mitigation_metrics.mitigated が True
- **THEN** 「対策済み (artifacts) — 直近 N 件検出」形式で表示する。件数ベースの提案は表示しない

#### Scenario: 未対策の推奨
- **WHEN** 対応する推奨の対策が未導入
- **THEN** 従来通り件数と改善提案を表示する

#### Scenario: 全対策済みかつ検出ゼロ
- **WHEN** 全ての recommendation_id 付き推奨が対策済みかつ recent_count が全て 0
- **THEN** 「ツール使用: 全対策済み — 検出なし」と1行で表示する

#### Scenario: 全対策済みだが検出あり
- **WHEN** 全ての推奨が対策済みだが recent_count > 0 のものがある
- **THEN** 各推奨の検出件数を個別表示する（1行表示にしない）
