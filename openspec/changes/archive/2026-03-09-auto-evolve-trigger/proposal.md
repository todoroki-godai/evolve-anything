## Why

現状 `/evolve` は手動実行が必要で、せっかく構築した測定（Gap 1 Ph0-2）+ 進化（Gap 2 Ph2-3）パイプラインが「ユーザーが忘れると動かない」状態にある。セッション終了時やスキル変更時に自動でトリガーすることで、進化ループを Zero-Touch 化し、環境品質の継続的な維持を実現する。

## What Changes

- **セッション終了時の自動 evolve 判定**: 既存の `session_summary.py` (Stop hook) を拡張し、evolve 実行条件を評価。条件を満たした場合に `pending-trigger.json` にトリガー結果を書き出し、次回 SessionStart 時にユーザーへ提案メッセージを配信
- **Corrections 蓄積閾値トリガー**: `correction_detect.py` (PostToolUse hook) で corrections 件数を監視し、閾値到達時に関連スキルの再最適化を提案
- **Session-end 評価での audit 統合**: セッション終了時に前回 audit からの経過日数を評価し、30日超の場合は audit 実行を提案（定期 cron の代替）
- **evolve 実行条件エンジン**: 複数のトリガー条件（セッション数、corrections 数、前回実行からの経過日数、audit 経過日数）を統合評価する共通モジュール
- **ユーザー設定**: トリガーの有効/無効、閾値のカスタマイズを `~/.claude/rl-anything/evolve-state.json` の `trigger_config` キーで管理

**NOTE**: 全トリガーは「提案」のみで自動実行はしない（Graduated Autonomy は Phase 5 で対応）。

## Capabilities

### New Capabilities
- `evolve-trigger-engine`: evolve 実行条件の評価エンジン。複数トリガー条件の統合判定、クールダウン管理、ユーザー設定の読み込みを担当
- `session-end-trigger`: セッション終了時の evolve トリガー。session_summary hook の拡張として実装。audit overdue 検出を含む
- `corrections-threshold-trigger`: corrections 蓄積時の再最適化トリガー。correction_detect hook の拡張として実装
- `pending-trigger-delivery`: Stop hook → SessionStart hook 間の遅延配信メカニズム。`pending-trigger.json` 経由で restore_state.py がメッセージを配信

### Modified Capabilities
- (なし — 既存 spec の要件変更はなく、hooks の実装拡張のみ)

## Impact

- **hooks/session_summary.py**: Stop hook にトリガー評価ロジックを追加。結果を `pending-trigger.json` に書き出し
- **hooks/restore_state.py**: SessionStart hook に `pending-trigger.json` 読取 + stdout 出力 + 削除ロジックを追加
- **hooks/correction_detect.py**: PostToolUse hook に蓄積閾値チェックを追加
- **scripts/lib/trigger_engine.py**: 新規共通モジュール（条件評価 + クールダウン + 設定管理）
- **~/.claude/rl-anything/evolve-state.json**: 既存ファイルに `trigger_config` キー + `trigger_history` + `last_audit_timestamp` フィールドを追加
- **~/.claude/rl-anything/pending-trigger.json**: Stop → SessionStart 間の遅延配信ファイル（ランタイム生成・配信後削除）
