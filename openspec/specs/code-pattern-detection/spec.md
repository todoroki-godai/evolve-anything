## ADDED Requirements

### Requirement: モジュール間変換パターンの検出

`detect_data_contract_verification(project_dir: Path)` は、プロジェクト内の Python/TypeScript ファイルを走査し、モジュール間 dict 変換パターン（glue コード）を検出しなければならない（MUST）。検出対象は `.py` および `.ts` ファイルで、`node_modules/`, `.venv/`, `__pycache__/` は除外しなければならない（MUST）。

#### Scenario: Python の dict 変換パターン検出
- **WHEN** プロジェクト内に `from X import Y` + 同一ファイル内で `{...}` による dict 構築パターンが3箇所以上ある
- **THEN** `applicable: True` を返さなければならない（MUST）。`evidence` に該当ファイルパスを含まなければならない（MUST）

#### Scenario: TypeScript の interface 変換パターン検出
- **WHEN** プロジェクト内に `import { X } from "Y"` + 同一ファイル内でオブジェクトリテラル `{ key: value }` による変換パターンが3箇所以上ある
- **THEN** `applicable: True` を返さなければならない（MUST）。`evidence` に該当ファイルパスを含まなければならない（MUST）

#### Scenario: 検出閾値未満
- **WHEN** 変換パターンが2箇所以下
- **THEN** `applicable: False` を返さなければならない（MUST）

### Requirement: 検出のパフォーマンス制約

検出関数はプロジェクト走査に最大5秒の制限を設けなければならない（MUST）。大規模リポジトリでは走査対象を `scripts/`, `src/`, `lib/`, `skills/` ディレクトリに限定しなければならない（MUST）。

#### Scenario: 大規模リポジトリでのタイムアウト防止
- **WHEN** プロジェクト内のファイル数が LARGE_REPO_FILE_THRESHOLD (1000) を超える
- **THEN** 優先ディレクトリ（scripts/, src/, lib/, skills/）のみ走査し、DETECTION_TIMEOUT_SECONDS (5秒) 以内に完了しなければならない（MUST）

### Requirement: 検出ツールのフォールバック

検出関数は rg (ripgrep) を優先使用するが、利用不可の場合はフォールバックしなければならない（MUST）。

#### Scenario: rg が利用不可
- **WHEN** rg コマンドが PATH に存在しない
- **THEN** Python の glob + re モジュールにフォールバックしなければならない（MUST）

#### Scenario: permission denied
- **WHEN** 走査対象ファイルの読み取り権限がない
- **THEN** 当該ファイルをスキップしなければならない（MUST）。例外を発生させてはならない（MUST NOT）

### Requirement: ルールテンプレートのプロジェクト適応

`verification_rule_candidate` の `rule_template` はプロジェクトの言語やフレームワークに応じてカスタマイズ可能としなければならない（MUST）。Python プロジェクトでは「Read で確認」、TypeScript プロジェクトでは「型定義を確認」のように表現を変えなければならない（MUST）。

#### Scenario: Python プロジェクトへのテンプレート適用
- **WHEN** プロジェクトの主要言語が Python（.py ファイルが最多）
- **THEN** ルールテンプレートに「ソース関数の返り値構造（dictキー・型）を Read で確認する」を含まなければならない（MUST）

#### Scenario: TypeScript プロジェクトへのテンプレート適用
- **WHEN** プロジェクトの主要言語が TypeScript（.ts/.tsx ファイルが最多）
- **THEN** ルールテンプレートに「ソース関数の戻り型（interface/type）を Read で確認する」を含まなければならない（MUST）

#### Scenario: 言語ファイル数同数時のデフォルト
- **WHEN** .py ファイル数と .ts/.tsx ファイル数が同数
- **THEN** Python テンプレートをデフォルトとしなければならない（MUST）

### Edge Cases

#### Scenario: バイナリファイルのみのプロジェクト
- **WHEN** project_dir 内に .py / .ts / .tsx ファイルが存在しない
- **THEN** `applicable: False` を返さなければならない（MUST）
