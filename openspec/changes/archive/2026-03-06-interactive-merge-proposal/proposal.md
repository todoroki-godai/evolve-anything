## Why

reorganize フェーズで類似度 0.4〜0.6 の範囲で検出されたスキルペアが、merge フェーズの閾値（0.60）未満として `skipped_low_similarity` で自動除外される。しかしユーザーが手動で統合すると妥当なケースがあり、機械的閾値だけでは統合機会を逃している（GitHub Issue #5）。

## What Changes

- reorganize の `merge_groups` で検出され `skipped_low_similarity` となったペアに対して、対話的に統合を提案するフローを追加
- ユーザーが却下したペアは merge suppression に登録し、次回以降の再提案を抑制
- evolve SKILL.md の Merge サブステップに interactive proposal ロジックを追記

## Capabilities

### New Capabilities
- `interactive-merge-proposal`: reorganize 検出かつ merge 閾値未満のペアに対して AskUserQuestion で統合を提案し、承認時に Claude が統合版を生成するフロー

### Modified Capabilities
- `merge`: `skipped_low_similarity` ペアの扱いを変更 — 一定類似度（0.40+）以上のペアは `status: "interactive_candidate"` として出力し、SKILL.md 側で対話的提案の対象とする
- `merge-group-filter`: interactive candidate 判定用の下限閾値（0.40）を設定可能にする

## Impact

- **コード**: `skills/prune/scripts/prune.py` の `merge_duplicates()` に interactive candidate status を追加
- **コード**: `scripts/lib/similarity.py` の `filter_merge_group_pairs()` に interactive 閾値パラメータを追加
- **スキル**: `skills/evolve/SKILL.md` の Step 5 Merge サブステップに interactive proposal フローを追記
- **設定**: `evolve-state.json` に `interactive_merge_similarity_threshold`（デフォルト 0.40）を追加
- **既存動作**: 閾値 0.60 以上のペアは従来通り `proposed`。0.40〜0.60 のペアが新たに `interactive_candidate` として対話提案される。0.40 未満は従来通り `skipped_low_similarity`
