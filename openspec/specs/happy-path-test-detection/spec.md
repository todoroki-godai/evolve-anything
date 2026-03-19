Related: #33

## ADDED Requirements

### Requirement: パイプライン検出関数の提供

`detect_happy_path_test_gap(project_dir: Path) -> Dict[str, Any]` は verification_catalog の検出関数インターフェースに従わなければならない（MUST）。返り値は `{"applicable": bool, "evidence": list[str], "confidence": float, "llm_escalation_prompt": Optional[str]}` とする。

#### Scenario: パイプラインコードが検出されテスト欠落がある場合
- **WHEN** プロジェクト内に3つ以上のステップ呼び出しを持つ関数が HAPPY_PATH_MIN_PATTERNS 箇所以上あり、かつ対応テストファイルにパイプライン関数名が含まれない
- **THEN** `applicable` が `True`、`evidence` にパイプライン関数のファイルパス（プロジェクトルートからの相対パス）を含まなければならない（MUST）。既存パターンとの一貫性のため関数名は含めない。`confidence` は 0.0-0.7 の範囲でなければならない（MUST）

#### Scenario: パイプラインコードが検出されるがテストが存在する場合
- **WHEN** パイプライン関数が検出され、対応テストファイルにパイプライン関数名が含まれる
- **THEN** `applicable` が `False` でなければならない（MUST）

#### Scenario: パイプラインコードが閾値未満の場合
- **WHEN** パイプライン関数が HAPPY_PATH_MIN_PATTERNS 未満
- **THEN** `applicable` が `False` でなければならない（MUST）

### Requirement: パイプライン検出パターン

パイプライン検出は以下の命名パターンの関数呼び出しが1つの関数内に3つ以上含まれるケースを対象としなければならない（MUST）: `step_*`, `phase_*`, `stage_*`, `layer_*`, `process_*`。`for ... in steps/phases/stages` のループパターンも検出しなければならない（MUST）。

#### Scenario: Python のパイプライン関数を検出
- **WHEN** Python ファイル内の関数に `step_validate()`, `step_transform()`, `step_save()` の3呼び出しがある
- **THEN** パイプライン関数として検出されなければならない（MUST）

#### Scenario: ループ型パイプラインを検出
- **WHEN** Python ファイル内に `for step in steps:` のようなループパターンがある
- **THEN** パイプライン関数として検出されなければならない（MUST）

#### Scenario: TypeScript のパイプライン関数を検出（camelCase）
- **WHEN** TypeScript ファイル内の関数に `await stepValidate()`, `await stepTransform()`, `await stepSave()` の3呼び出しがある（camelCase 命名、regex: `await\s+(?:step|phase|stage|layer|process)\w+\(`）
- **THEN** パイプライン関数として検出されなければならない（MUST）

### Requirement: テストファイル対応の解決

パイプライン関数が検出されたソースファイルに対して、テストファイルの探索は以下の規則に従わなければならない（MUST）:
- Python: `test_{filename}.py` または `{filename}_test.py`（同ディレクトリ + ソース親の tests/ サブディレクトリ + プロジェクトルート直下の tests/）
- TypeScript: `{filename}.test.ts` / `{filename}.test.tsx`（同ディレクトリ + `__tests__/` サブディレクトリ）

#### Scenario: テストファイルが同ディレクトリにある場合
- **WHEN** `src/pipeline.py` にパイプライン関数があり `src/test_pipeline.py` が存在する
- **THEN** `src/test_pipeline.py` をテストファイルとして解決しなければならない（MUST）

#### Scenario: テストファイルがソース親の tests/ サブディレクトリにある場合
- **WHEN** `src/pipeline.py` にパイプライン関数があり `src/tests/test_pipeline.py` が存在する
- **THEN** `src/tests/test_pipeline.py` をテストファイルとして解決しなければならない（MUST）

#### Scenario: テストファイルがプロジェクトルートの tests/ にある場合
- **WHEN** `src/pipeline.py` にパイプライン関数があり `tests/test_pipeline.py` が存在する
- **THEN** `tests/test_pipeline.py` をテストファイルとして解決しなければならない（MUST）

### Requirement: エラーハンドリング

検出関数はいかなる場合も例外を呼び出し元に伝播させてはならない（MUST NOT）。

#### Scenario: ファイル読み取りエラー
- **WHEN** ソースファイルの読み取りで PermissionError が発生する
- **THEN** 当該ファイルをスキップし処理を続行しなければならない（MUST）

#### Scenario: project_dir が存在しない
- **WHEN** 指定された project_dir が存在しない
- **THEN** `{"applicable": False, "evidence": [], "confidence": 0.0}` を返さなければならない（MUST）

### Requirement: ルールテンプレートの提供

VERIFICATION_CATALOG に `happy-path-test-verification` エントリを追加しなければならない（MUST）。`rule_template` は「オーケストレーション・パイプライン等の複数ステップを持つコードは、全ステップを通る正常系E2Eテストを最初に書く」旨の3行以内テンプレートとする。

#### Scenario: カタログエントリの構造
- **WHEN** `VERIFICATION_CATALOG` をインポートする
- **THEN** `id: "happy-path-test-verification"`, `type: "rule"`, `detection_fn: "detect_happy_path_test_gap"`, `applicability: "conditional"`, `rule_filename: "test-happy-path-first.md"` のエントリが含まれなければならない（MUST）

### Requirement: RECOMMENDED_ARTIFACTS エントリ

discover の `RECOMMENDED_ARTIFACTS` に `test-happy-path-first` エントリを追加しなければならない（MUST）。`path` は `~/.claude/rules/test-happy-path-first.md` とする。

#### Scenario: ルール未導入プロジェクトへの提案
- **WHEN** `~/.claude/rules/test-happy-path-first.md` が存在しない
- **THEN** 未導入アーティファクトとしてレポートに含まれなければならない（MUST）

#### Scenario: ルール導入済みプロジェクト
- **WHEN** `~/.claude/rules/test-happy-path-first.md` が既に存在する
- **THEN** 未導入リストに含まれてはならない（MUST NOT）

### Requirement: content-aware 導入済み検出

`_CONTENT_KEYWORDS_MAP` に `happy-path-test-verification` エントリを登録しなければならない（MUST）。キーワードは `["ハッピーパス", "happy path", "E2Eテスト", "正常系テスト"]` とする。

#### Scenario: 別名ルールでハッピーパス検証が導入済み
- **WHEN** `test-happy-path-first.md` は存在しないが、別の rules ファイルに「ハッピーパス」キーワードが含まれる
- **THEN** 導入済みと判定されなければならない（MUST）
