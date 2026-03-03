## Why

backfill の intent 分類でキーワードマッチの限界により 73.9% が "other" に分類される。
現状の Step 2 は手動で extract → 目視分類 → apply の3ステップが必要で、実運用では省略されがち。
LLM（haiku）で自動分類すれば、コストを抑えつつ分類精度を大幅に改善できる。

## What Changes

- `backfill.py`: `user_prompts` をセッションメタデータに記録（reclassify が参照するため必須）。`MAX_PROMPT_LENGTH` を 200→500 に拡大（短すぎて分類困難なプロンプトを減らす）
- `reclassify.py`: `auto` サブコマンドを追加。`claude -p --model haiku` でバッチ分類し、結果を自動 apply
- `SKILL.md`: Step 2 を `reclassify.py auto` による自動実行に更新

## Capabilities

### New Capabilities
- `auto-reclassify`: "other" intent を LLM（haiku）で自動分類し、extract → classify → apply を一括実行する機能

### Enhanced Capabilities
- `user-prompts-recording`: backfill 時にユーザープロンプト原文をセッションメタデータに保存し、後続の再分類で利用可能にする

## Impact

- `skills/backfill/scripts/backfill.py` — `MAX_PROMPT_LENGTH` 変更、`user_prompts` フィールド追加
- `skills/backfill/scripts/reclassify.py` — `auto` サブコマンド追加
- `skills/backfill/SKILL.md` — Step 2 手順更新
- 既存テスト: `test_agent_prompt_truncated` のアサーション値変更（200→500）
