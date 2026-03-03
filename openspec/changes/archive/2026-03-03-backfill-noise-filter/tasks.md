## 1. ノイズフィルタ実装

- [x] 1.1 `backfill.py` に `_classify_system_message(content: str)` を追加（返値: `None` / `("skill-invocation", name)` / `("passthrough", content)`）
- [x] 1.2 `parse_transcript()` の human メッセージ処理で `_classify_system_message()` を呼び出し、結果に応じて `user_prompts` / `user_intents` の記録を分岐
- [x] 1.3 `parse_transcript()` の返値サマリに `filtered_messages` カウンターを追加

## 2. reclassify.py 改修

- [x] 2.1 `auto` サブコマンド・`_build_classify_prompt()` ・`_call_claude_classify()` ・`auto_reclassify()` を削除
- [x] 2.2 `extract` に `--include-reclassified` フラグ追加（`reclassified_intents` 内の残 "other" も抽出）
- [x] 2.3 `VALID_CATEGORIES` に `skill-invocation` を追加

## 3. SKILL.md Step 2 書き換え

- [x] 3.1 Step 2 を Claude Code ネイティブ LLM による分類手順に書き換え（extract → Claude Code 分類 → apply の3ステップ）

## 4. テスト

- [x] 4.1 `_classify_system_message()` のユニットテスト（中断シグナル、command-name 抽出、local-command、task-notification、通常プロンプト通過、角括弧の非中断プロンプト、command-name パース失敗）
- [x] 4.2 `parse_transcript()` の統合テスト（フィルタ後の `user_prompts` / `user_intents`、`filtered_messages` カウント、content リスト形式でのフィルタ動作、複数パターン混在（command-name + 通常テキスト + 中断シグナルが同一セッション内に共存））
- [x] 4.3 `extract --include-reclassified` のテスト
- [x] 4.4 `auto` サブコマンド関連のテストを削除
