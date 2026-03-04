## Why

evolve の Prune フェーズ（Merge サブステップ）で却下した統合候補ペアが、次回 evolve 実行時に再度提案される。現在の `discover-suppression.jsonl` は discover.py のパターン検出（behavior/error/rejection/session）にのみ効果があり、prune の `merge_duplicates()`（semantic similarity 由来）には適用されない。意図的に分離しているスキル同士が毎回統合候補に出てしまい、手動スキップが必要になる。

## What Changes

- `merge_duplicates()` に merge suppression チェックを追加し、却下済みペアをフィルタリング
- merge 却下時に suppression エントリを永続化する関数を追加
- suppression データは既存の `discover-suppression.jsonl` を拡張するか、merge 専用ファイルを新設

## Capabilities

### New Capabilities
- `merge-suppression`: merge 統合候補の却下を記録・永続化し、次回以降の evolve で再提案を抑制する機能

### Modified Capabilities
- `merge`: merge_duplicates() に suppression フィルタリングを追加

## Impact

- `skills/prune/scripts/prune.py` — `merge_duplicates()` に suppression チェック追加
- `skills/discover/scripts/discover.py` — suppression ファイル I/O の共通化（必要に応じて）
- `skills/evolve/SKILL.md` — merge 却下時の suppression 登録フロー明確化
- `~/.claude/rl-anything/discover-suppression.jsonl` or 新規ファイル — suppression データ格納先
