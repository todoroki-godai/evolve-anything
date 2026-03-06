## Why

`/rl-anything:optimize` 実行後に accept/reject の確認フローが SKILL.md に記載されておらず、`history.jsonl` の全エントリが `human_accepted: null` のまま残り続ける。これにより `/rl-anything:evolve` の Step 6 (Fitness Evolution) が常に「データ不足: 0/30件」と表示され、評価関数の改善が一切機能しない。CLI 側の `--accept` / `--reject` フラグと `record_human_decision()` は実装済みだが、スキルのワークフローに組み込まれていない。

## What Changes

- `skills/genetic-prompt-optimizer/SKILL.md` の Step 3 に accept/reject 確認フローを追加
  - 最適化結果を提示後、AskUserQuestion で accept/reject を確認
  - 結果に応じて `optimize.py --target <TARGET> --accept` or `--reject --reason "..."` を実行
- `skills/genetic-prompt-optimizer/SKILL.md` の frontmatter `allowed-tools` に `AskUserQuestion` を追加

## Capabilities

### New Capabilities

（なし — 既存機能のワークフロー修正のみ）

### Modified Capabilities

（既存 spec なし）

## Impact

- **skills/genetic-prompt-optimizer/SKILL.md**: Step 3 のワークフロー拡張 + allowed-tools 追加
- **skills/genetic-prompt-optimizer/scripts/optimize.py**: 変更なし（既に `--accept`/`--reject`/`record_human_decision()` 実装済み）
- **skills/evolve-fitness/**: 変更なし（`history.jsonl` にデータが蓄積されれば自動的に機能する）
- **skills/evolve/SKILL.md**: 変更なし（Step 6 は既に正しく記述されている）
