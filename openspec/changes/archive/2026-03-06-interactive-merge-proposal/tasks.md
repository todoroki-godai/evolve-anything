## 1. similarity.py の拡張

- [x] 1.1 `filter_merge_group_pairs()` の返り値を `(passed, interactive)` タプルに変更。interactive 閾値パラメータ `interactive_threshold` を追加
- [x] 1.2 interactive 閾値以上 かつ merge 閾値未満のペアを `interactive` リスト（`List[tuple[frozenset[str], float]]`）として返却するロジックを実装
- [x] 1.3 `filter_merge_group_pairs()` の既存テスト 5件をタプル返却に対応するよう修正（`scripts/tests/test_similarity.py`）
- [x] 1.4 `filter_merge_group_pairs()` の新規テストを追加（medium similarity → interactive リスト、sklearn 未インストール時は interactive 空リスト）

## 2. prune.py の拡張

- [x] 2.1 `load_interactive_merge_threshold()` を追加（`evolve-state.json` の `interactive_merge_similarity_threshold`、デフォルト 0.40）
- [x] 2.2 `merge_duplicates()` 内の `filter_merge_group_pairs()` 呼び出しを新シグネチャに対応。`interactive` ペアを `status: "interactive_candidate"` + `similarity_score` 付きで `merge_proposals` に追加
- [x] 2.3 `skipped_low_similarity` の出力は interactive 閾値未満のペアのみとなるよう調整
- [x] 2.4 `merge_duplicates()` のテストを追加（interactive_candidate の出力、similarity_score の検証、既存 proposed/skipped フローの回帰テスト）

## 3. evolve SKILL.md の更新

- [x] 3.1 Step 5 Merge サブステップに `interactive_candidate` のハンドリングを追記。similarity_score 降順で最大3件を AskUserQuestion で提案するフローを記述
- [x] 3.2 承認時: proposed と同じフロー（統合版生成 → primary に上書き → secondary をアーカイブ）を適用する旨を記述
- [x] 3.3 却下時: `add_merge_suppression()` で suppression 登録する旨を記述

## 4. テストと検証

- [x] 4.1 全テストスイート実行（`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v`）で既存テストの回帰なしを確認
- [x] 4.2 CHANGELOG.md にエントリ追加、plugin.json のバージョン bump
