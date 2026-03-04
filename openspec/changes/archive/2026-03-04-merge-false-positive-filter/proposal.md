## Why

evolve パイプラインの Merge フェーズで、`reorganize.merge_groups` のクラスタ内スキル全ペアに対して C(N,2) の統合提案を生成するため、同じドメイン語彙を共有するが異なる責務のスキル群から大量の偽陽性が発生する。v0.15.1 で `duplicate_candidates` のスタブ問題は解消したが、reorganize 経由のクラスタ起因の偽陽性は未対処。実ユーザーから偽陽性率 95% の報告あり（GitHub Issue #4）。

## What Changes

- reorganize の `merge_groups` からマージ候補を生成する際に、ペア単位の TF-IDF コサイン類似度チェックを追加し、閾値未満のペアを除外する

## Capabilities

### New Capabilities

- `merge-group-filter`: reorganize の merge_groups からマージ候補ペアを生成する際のフィルタリングロジック。ペア単位の TF-IDF コサイン類似度チェックを提供する

### Modified Capabilities

- `merge`: merge_duplicates() が reorganize_merge_groups を処理する際に、新しいフィルタリングパイプラインを通すよう変更

## Impact

- `skills/prune/scripts/prune.py` — `merge_duplicates()` 関数の reorganize_merge_groups 処理パス
- `scripts/lib/similarity.py` — ペア単位の類似度計算ユーティリティの追加
- `skills/reorganize/scripts/reorganize.py` — 変更なし（merge_groups 出力はそのまま使用）
- 既存のマージ抑制機構（`discover-suppression.jsonl`）はそのまま活用
