## MODIFIED Requirements

### Requirement: Session text analysis in discover
discover.py に `--session-scan` オプションを追加し、セッション JSONL のユーザーメッセージテキストから繰り返しパターンを検出する（SHALL）。既存の usage.jsonl ベースの分析と補完的に動作する。

#### Scenario: Discover misses text-only pattern
- **WHEN** ユーザーが毎回手動で "git log --oneline -20" を Bash で入力し、usage.jsonl にスキル記録がない
- **THEN** `discover --session-scan` がセッション JSONL からパターンを検出し候補を提案する

#### Scenario: Session scan with threshold
- **WHEN** `discover --session-scan` を実行し、"deploy" が計8回出現する
- **THEN** "deploy" パターンをスキル候補として提案する（閾値: 5回以上）

### Requirement: Backfill session parsing reuse
セッションテキスト分析は backfill.py の `parse_transcript()` を利用する（SHALL）。独自パーサーを実装してはならない。

#### Scenario: Session file parsing
- **WHEN** discover --session-scan がセッションファイルを分析する
- **THEN** backfill.py の parse_transcript() を使用し、ユーザーメッセージを抽出する

### Requirement: Failure scenarios
セッション不在やパースエラーを安全に処理する（MUST）。

#### Scenario: No session files available
- **WHEN** セッション JSONL ファイルが1つも存在しない
- **THEN** セッションスキャン結果は空として、他の分析結果のみを返す

#### Scenario: Session parse error
- **WHEN** `parse_transcript()` がセッションファイルのパース中に例外を送出する
- **THEN** そのセッションをスキップし、stderr に警告を出力して残りのセッションの分析を続行する
