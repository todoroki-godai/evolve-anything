## Why

rl-anything の observe hooks はツール呼び出しを記録するが、ユーザーの修正意図（「いや、」「違う、」）やスキルの価値減衰を捉えられない。claude-reflect が実証した CJK パターン検出・信頼度スコア・2段階検証のアーキテクチャを取り入れることで、discover/prune の精度を大幅に改善できる。

## What Changes

- **observe hooks に CJK 修正パターン検出を追加**: UserPromptSubmit hook で「いや、」「違う、」「そうじゃなくて」等の修正発話を検出し、直前のスキル実行と紐付けて `corrections.jsonl` に記録
- **信頼度スコア + decay を prune に導入**: usage レコードに confidence フィールドを追加し、時間経過で減衰させる。prune の淘汰判定で「最近使われていない」だけでなく「信頼度が閾値以下」も条件に
- **analyze に 2段階検証を追加**: 高速パターンマッチ（hooks）で候補を絞り、analyze 実行時に LLM セマンティック検証で確認する 2 段階パイプライン
- **recommendation routing の多ターゲット化**: analyze の推奨アクションを skills / rules / CLAUDE.md / memory に振り分け

## Capabilities

### New Capabilities
- `correction-detection`: UserPromptSubmit hook で CJK/英語の修正パターンを検出し corrections.jsonl に記録
- `confidence-decay`: usage/workflow レコードに信頼度スコアを付与し、時間経過で減衰させる仕組み
- `semantic-validation`: analyze 実行時に corrections + usage データを LLM で検証し、誤検出を除外

### Modified Capabilities
- `backfill`: corrections.jsonl の生成に対応（既存セッションから修正パターンを遡及抽出）
- `reclassify`: confidence スコアを intent 分類に反映

## Impact

- hooks/: `UserPromptSubmit` hook 新設、observe.py に confidence フィールド追加
- skills/backfill/: corrections 遡及抽出ロジック追加
- skills/prune/: decay ベースの淘汰判定ロジック変更
- skills/analyze/: semantic validation + multi-target routing 追加
- データスキーマ: usage.jsonl に `confidence` フィールド、corrections.jsonl 新設
