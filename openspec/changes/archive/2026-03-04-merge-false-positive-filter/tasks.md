## 1. 類似度フィルタ関数の実装

- [x] 1.1 `scripts/lib/similarity.py` に `filter_merge_group_pairs(skills, skill_path_map, threshold)` 関数を追加。merge_group のスキルリストを受け取り、ペア単位の TF-IDF コサイン類似度を計算し、閾値以上のペアのみを返す
- [x] 1.2 sklearn 未インストール時の graceful degradation を実装（全ペアをそのまま返す）

## 2. merge_duplicates() への統合

- [x] 2.1 `skills/prune/scripts/prune.py` の `merge_duplicates()` 内、reorganize_merge_groups のペア展開ループ（L593-599）を `filter_merge_group_pairs()` 呼び出しに置き換え
- [x] 2.2 `evolve-state.json` から `reorganize_merge_similarity_threshold` を読み込み、フィルタ閾値として渡す。未設定時はデフォルト 0.60

## 3. テスト

- [x] 3.1 `filter_merge_group_pairs()` の単体テスト：高類似度ペア通過、低類似度ペア除外、大規模クラスタでの偽陽性削減
- [x] 3.2 `merge_duplicates()` の統合テスト：reorganize 由来ペアがフィルタリングされることの確認
- [x] 3.3 sklearn 未インストール時のフォールバックテスト
- [x] 3.4 既存テストの全パス確認

## 4. 既存 spec の更新

- [x] 4.1 `openspec/specs/merge/spec.md` に reorganize 由来ペアのフィルタリング要件を追記（archive 時に自動適用）
