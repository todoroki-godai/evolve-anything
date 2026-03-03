## Why

backfill + reclassify の実データ検証（4プロジェクト、6,553 intents）で3つの問題が判明した：

1. **ノイズ混入**: システムメッセージ（中断シグナル、コマンドタグ、ローカルコマンド出力）が `user_prompts` に混入し、LLM 再分類で大量の "conversation" 誤分類を生む
2. **subprocess アーキテクチャの非効率**: `claude -p --model haiku` を subprocess で呼ぶ設計は、Claude Code Max 環境ではセッション内 LLM を直接使えば不要。コスト・レイテンシ・依存の全面でデメリット
3. **再分類スキップ問題**: `reclassified_intents` が存在するセッションは `extract_other_intents()` がスキップするため、初回分類で "other" のまま残った intent を再分類できない（docs-platform: 残 1,547 件）

## What Changes

- `backfill.py`: `user_prompts` / `user_intents` 記録前にシステムメッセージをフィルタ
  - `[Request interrupted by user]` → 除外
  - `<command-name>` タグ → コマンド名抽出、intent `skill-invocation`
  - `<local-command-*>` / `<task-notification>` → 除外
- `reclassify.py`: `auto` サブコマンド削除。`extract` に `--include-reclassified` オプション追加（既分類セッションの残 "other" も抽出可能に）
- `SKILL.md`: Step 2 を Claude Code ネイティブ LLM による分類に変更（subprocess 廃止）
- `common.py`: `VALID_CATEGORIES` に `skill-invocation` 追加

## Capabilities

### New Capabilities
- `system-message-filter`: backfill 時にシステムメッセージを識別しフィルタリングする機能
- `reclassify`: reclassify の完成形仕様。subprocess 廃止 → Claude Code ネイティブ LLM 分類、`extract --include-reclassified` 追加、`auto` サブコマンド削除、`VALID_CATEGORIES` に `skill-invocation` 追加

### Modified Capabilities
- `backfill`: `user_prompts` / `user_intents` の記録ロジックにフィルタ追加、サマリに `filtered_messages` 追加

## Impact

- `skills/backfill/scripts/backfill.py` — フィルタロジック追加
- `skills/backfill/scripts/reclassify.py` — `auto` サブコマンド削除、`extract` 拡張
- `skills/backfill/SKILL.md` — Step 2 手順の全面書き換え
- `hooks/common.py` — `VALID_CATEGORIES` 更新
- `skills/backfill/scripts/tests/` — フィルタテスト追加、auto テスト削除
- 既存 `llm-auto-intent-classify` の specs/design との整合性更新
