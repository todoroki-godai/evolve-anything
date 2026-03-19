Related: #33

## MODIFIED Requirements

### Requirement: カタログモジュールの提供

`scripts/lib/verification_catalog.py` は VERIFICATION_CATALOG リストを公開しなければならない（MUST）。各エントリは以下のフィールドを持つ dict とする: `id` (str), `type` (str: "rule"), `description` (str), `rule_template` (str), `detection_fn` (Optional[str]), `applicability` (str: "always" | "conditional"), `rule_filename` (str)。`rule_filename` は catalog 内で一意でなければならない（MUST）。

#### Scenario: カタログの初期エントリ
- **WHEN** `VERIFICATION_CATALOG` をインポートする
- **THEN** `data-contract-verification` エントリが含まれなければならない（MUST）。`rule_template` は3行以内の Markdown ルールテンプレートであること

#### Scenario: ハッピーパステスト検証エントリ
- **WHEN** `VERIFICATION_CATALOG` をインポートする
- **THEN** `happy-path-test-verification` エントリが含まれなければならない（MUST）。`detection_fn` は `"detect_happy_path_test_gap"` でなければならない（MUST）
