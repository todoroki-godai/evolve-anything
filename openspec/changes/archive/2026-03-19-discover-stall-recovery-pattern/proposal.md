Closes: #35

## Why

長時間プロセス（CDK deploy, Docker build, npm install 等）がバックグラウンドで停滞し、ユーザーが「まだ？」→ 原因調査 → kill → リトライを繰り返すパターンが複数ユーザーで発生している（#35）。現在の discover はツール使用パターン（builtin_replaceable, sleep_polling）やスキル未使用を検出するが、**プロセス停滞→手動リカバリの繰り返し**は検出できない。このパターンを自動検出し、Pre-flight チェックやリカバリルールを提案することで、同じ失敗の繰り返しを防止する。

## What Changes

- **セッションtranscript からプロセス停滞パターンを検出する関数を追加**: `bash(long_command)` → `error/timeout` → `bash(pgrep/ps)` → `bash(kill)` → `bash(long_command)` の繰り返しシーケンスを検出
- **RECOMMENDED_ARTIFACTS にプロセスガードエントリを追加**: 停滞パターン検出時に Pre-flight チェック（既存プロセス確認→kill→実行）を提案
- **discover の run_discover 結果に `stall_recovery_patterns` フィールドを追加**: evolve レポートで表示
- **pitfall_manager との統合**: 検出パターンを pitfall candidate として自動登録し、スキルの pitfalls.md に永続化

## Capabilities

### New Capabilities
- `stall-recovery-detection`: セッションtranscript（`~/.claude/projects/<encoded>/*.jsonl`）からプロセス停滞→手動リカバリの繰り返しパターンを検出し、rule/hook/pitfall 候補を生成する

### Modified Capabilities

## Impact

- `skills/discover/scripts/discover.py`: `run_discover()` に stall_recovery_patterns フィールド追加、RECOMMENDED_ARTIFACTS にエントリ追加
- `scripts/lib/tool_usage_analyzer.py`: 停滞パターン検出関数の追加
- `skills/evolve/scripts/evolve.py`: Diagnose レポートに停滞パターンセクション追加
- `scripts/lib/issue_schema.py`: `make_stall_recovery_issue()` factory 追加（既存 remediation パイプライン統合用）
- `scripts/lib/pitfall_manager.py`: 停滞パターンから pitfall candidate への変換統合
