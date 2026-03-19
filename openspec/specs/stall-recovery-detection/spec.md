## ADDED Requirements

### Requirement: Stall-recovery pattern detection from session transcripts
`detect_stall_recovery_patterns()` 関数はセッションtranscript（`~/.claude/projects/<encoded>/*.jsonl`）の Bash コマンドシーケンスを分析し、プロセス停滞→手動リカバリの繰り返しパターンを検出しなければならない（SHALL）。

#### Scenario: CDK deploy stall-recovery detected
- **WHEN** セッションtranscript に以下のシーケンスを含むセッションが 2 件以上存在する: `bash(cdk deploy)` → `bash(pgrep cdk)` → `bash(kill)` → `bash(cdk deploy)`
- **THEN** `stall_recovery_patterns` に `command_pattern: "cdk deploy"`, `session_count >= 2`, `recovery_actions: ["kill"]` を含むエントリが返される

#### Scenario: Single occurrence is not detected
- **WHEN** 停滞→リカバリシーケンスが 1 セッションでのみ発生している
- **THEN** `stall_recovery_patterns` は空リストを返す

#### Scenario: Normal restart without investigation is not detected
- **WHEN** `bash(cdk deploy)` → `bash(cdk deploy)` のように Investigation step（pgrep/ps 等）を挟まずに再実行されている
- **THEN** 停滞パターンとして検出されない（意図的な再実行と区別）

#### Scenario: Recency window filters old sessions
- **WHEN** セッションファイルの mtime が `STALL_RECOVERY_RECENCY_DAYS`（30日）より前である
- **THEN** そのセッションの停滞パターンは検出対象から除外される

#### Scenario: Insufficient data returns empty list
- **WHEN** セッションtranscript が存在しない、またはセッションディレクトリが空である
- **THEN** 空リストを返し、エラーは発生しない

### Requirement: Session-scoped command extraction
`extract_tool_calls_by_session()` 関数はセッションtranscript からセッション単位で Bash コマンドを抽出し、`Dict[str, List[str]]`（session_id → commands）を返さなければならない（SHALL）。

#### Scenario: Commands grouped by session
- **WHEN** 複数のセッションファイルが存在する
- **THEN** 各セッションファイルのコマンドが session_id（ファイル名 stem）をキーとして分離される

#### Scenario: Session file mtime used for recency
- **WHEN** `max_age_days` パラメータが指定されている
- **THEN** mtime が `max_age_days` 日以上前のセッションファイルは除外される

### Requirement: Long command pattern matching
検出対象の長時間コマンドは `LONG_COMMAND_PATTERNS` 定数で定義され、正規表現パターンとして管理されなければならない（SHALL）。

#### Scenario: Known long commands are matched
- **WHEN** Bash step のコマンドが `cdk deploy`, `docker build`, `npm install`, `pip install`, `yarn install`, `cargo build` のいずれかにマッチする
- **THEN** 当該 step が長時間コマンドとして分類される

#### Scenario: Custom command patterns
- **WHEN** `LONG_COMMAND_PATTERNS` に新しいパターンが追加される
- **THEN** 追加されたパターンも検出対象となる

### Requirement: Investigation and recovery command classification
Investigation コマンド（`pgrep`, `ps`, `lsof`, `fuser`）と Recovery コマンド（`kill`, `pkill`, `rm -rf`）は定数で分類されなければならない（SHALL）。

#### Scenario: Investigation commands detected
- **WHEN** Bash step のコマンドが `pgrep`, `ps aux`, `ps -ef`, `lsof`, `fuser` のいずれかを含む
- **THEN** Investigation step として分類される

#### Scenario: Recovery commands detected
- **WHEN** Bash step のコマンドが `kill`, `pkill`, `killall` のいずれかを含む
- **THEN** Recovery step として分類される

### Requirement: discover integration
`run_discover()` の結果に `stall_recovery_patterns` フィールドが含まれなければならない（SHALL）。

#### Scenario: Discover output includes stall patterns
- **WHEN** `run_discover()` が実行される
- **THEN** 結果 dict に `stall_recovery_patterns` キーが存在し、検出されたパターンのリスト（空リスト含む）が格納される

### Requirement: RECOMMENDED_ARTIFACTS entry for process guard
`RECOMMENDED_ARTIFACTS` にプロセスガードルールのエントリが含まれなければならない（SHALL）。

#### Scenario: Process guard recommendation
- **WHEN** プロセスガードルール（長時間コマンド実行前の既存プロセス確認）が未導入の場合
- **THEN** `detect_recommended_artifacts()` の結果に `process-stall-guard` エントリが含まれる

### Requirement: issue_schema integration
検出された停滞パターンは `make_stall_recovery_issue()` で issue_schema 準拠の dict に変換されなければならない（SHALL）。

#### Scenario: Issue generation from detected pattern
- **WHEN** `stall_recovery_patterns` に 1 件以上のパターンが存在する
- **THEN** 各パターンに対応する issue dict が生成され、`issue_type: "stall_recovery_candidate"`, `scope: "project"`, `confidence` フィールドを含む

#### Scenario: Confidence calculated from session count
- **WHEN** パターンの `session_count` が N である
- **THEN** `confidence = min(0.5 + N * 0.1, 0.95)` で算出される（例: 2 sessions → 0.7, 5+ sessions → 0.95）

### Requirement: Evolve report display
evolve レポートに停滞パターン検出セクションが表示されなければならない（SHALL）。

#### Scenario: Stall patterns in evolve report
- **WHEN** discover が 1 件以上の停滞パターンを検出した
- **THEN** evolve レポートの Diagnose セクションに「Process Stall Patterns」セクションが表示され、コマンドパターン・セッション数・推奨アクションが含まれる

### Requirement: pitfall candidate output
検出された停滞パターンから pitfall candidate が生成されなければならない（SHALL）。

#### Scenario: Pitfall candidate generation
- **WHEN** `stall_recovery_patterns` に 1 件以上のパターンが存在する
- **THEN** 各パターンに対応する pitfall candidate が `root_cause: "stall_recovery — {command_pattern}: {session_count} sessions"` 形式で生成される

#### Scenario: Duplicate pitfall deduplication
- **WHEN** 同一の command_pattern に対する pitfall candidate が既に存在する
- **THEN** `find_matching_candidate()` の Jaccard 重複排除により新規候補として追加されず、既存候補の Occurrence-count が増加する
