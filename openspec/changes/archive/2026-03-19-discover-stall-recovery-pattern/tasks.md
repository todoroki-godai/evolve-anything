Closes: #35

## 1. Core Detection Module

- [x] 1.1 `tool_usage_analyzer.py` に `LONG_COMMAND_PATTERNS`, `INVESTIGATION_COMMANDS`, `RECOVERY_COMMANDS` 定数を追加
- [x] 1.2 `extract_tool_calls_by_session()` 関数を実装: セッションtranscript からセッション単位で Bash コマンドを抽出し `Dict[str, List[str]]`（session_id → commands）を返す。既存 `extract_tool_calls()` の JSONL パースロジックを再利用
- [x] 1.3 `detect_stall_recovery_patterns(session_commands)` 関数を実装: session_commands（`Dict[str, List[str]]`）から Long→Investigation→Recovery→Long パターンを検出
- [x] 1.4 recency フィルタを実装: `STALL_RECOVERY_RECENCY_DAYS = 30`、セッションファイル mtime ベースで古いセッションを除外
- [x] 1.5 confidence 算出ロジックを実装: `confidence = min(0.5 + session_count * 0.1, 0.95)`
- [x] 1.6 テスト: 正常検出（2セッション以上）/ 単一セッション除外 / Investigation なし除外 / recency フィルタ除外 / confidence 算出検証 / 空データ（データ不足時は空リスト返却、エラーなし）

## 2. issue_schema Integration

- [x] 2.1 `issue_schema.py` に `STALL_RECOVERY_CANDIDATE` 定数と `make_stall_recovery_issue()` factory 関数を追加
- [x] 2.2 テスト: issue dict の構造検証（issue_type, scope, confidence フィールド）

## 3. discover Integration

- [x] 3.1 `discover.py` の `run_discover()` に `detect_stall_recovery_patterns()` 呼び出しを追加し、結果を `stall_recovery_patterns` フィールドに格納
- [x] 3.2 `RECOMMENDED_ARTIFACTS` に `process-stall-guard` エントリを追加（recommendation_id, content_patterns 付き）
- [x] 3.3 テスト: run_discover 結果に stall_recovery_patterns フィールドが存在すること、RECOMMENDED_ARTIFACTS のエントリ検証

## 4. evolve Report Display

- [x] 4.1 `evolve.py` の Diagnose レポートに「Process Stall Patterns」セクションを追加（コマンドパターン・セッション数・推奨アクション表示）
- [x] 4.2 stall_recovery_patterns を issue_schema 経由で remediation パイプラインに接続
- [x] 4.3 テスト: レポート表示の E2E 検証（パターンあり/なし）

## 5. pitfall_manager Integration

- [x] 5.1 停滞パターンから pitfall candidate への変換ロジックを実装: root_cause フォーマット `"stall_recovery — {command_pattern}: {session_count} sessions"`
- [x] 5.2 `find_matching_candidate()` による Jaccard 重複排除を統合
- [x] 5.3 テスト: pitfall candidate 生成 / 重複排除 / root_cause フォーマット検証
