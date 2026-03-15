## MODIFIED Requirements

### Requirement: 行数制限チェックの共通モジュール

`scripts/lib/line_limit.py` に行数制限チェックの共通実装を提供しなければならない（MUST）。定数 `MAX_SKILL_LINES`（500）、`MAX_RULE_LINES`（3）、`MAX_PROJECT_RULE_LINES`（5）を Single Source of Truth として定義する。

#### Scenario: 共通関数のインターフェース
- **WHEN** `check_line_limit(target_path, content)` を呼び出す
- **THEN** コンテンツの行数がファイル種別に応じた上限以下なら `True`、超過なら `False` を返す

**注記**: `MEMORY_STALE_DAYS` 定数と `check_memory_staleness()` 関数は `scripts/lib/layer_diagnose.py` に配置する（line_limit.py は行数制限の責務であり、staleness 検出とは無関係なため）。

#### Scenario: Memory staleness by frontmatter timestamp
- **WHEN** memory ファイルの frontmatter に `last_modified` フィールドがある
- **AND** その値が 90 日以上前である
- **THEN** `check_memory_staleness(path)` が `True` を返す（MUST: frontmatter を mtime より優先）

#### Scenario: Memory staleness by mtime fallback
- **WHEN** memory ファイルの frontmatter に `last_modified` がない
- **AND** ファイルの mtime が 90 日以上前である
- **THEN** `check_memory_staleness(path)` が `True` を返す

#### Scenario: Recent memory file
- **WHEN** memory ファイルの mtime が 90 日未満である
- **THEN** `check_memory_staleness(path)` が `False` を返す

#### Scenario: Git operation mtime reset detection
- **WHEN** 対象ディレクトリ内の全ファイルの mtime の標準偏差が 60 秒未満である
- **THEN** git clone/checkout 直後と判断し mtime ベースの staleness チェックをスキップする（SHOULD）
