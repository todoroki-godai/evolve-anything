## 0. Spec 更新（実装の先行条件）

- [x] 0.1 既存の merge spec（`openspec/specs/merge/spec.md`）に `skipped_suppressed` status を追加更新（`merged_content_preview` は `status: "proposed"` のみ、`skipped_*` では省略の注記を含む）

## 1. Suppression I/O の拡張

- [x] 1.1 `discover.py` に `load_merge_suppression()` 関数を追加（`type: "merge"` エントリのみを抽出し、ペアキー文字列の set を返す）
- [x] 1.2 `discover.py` に `add_merge_suppression(skill_a: str, skill_b: str)` 関数を追加（スキル名をソートし `::` 結合、`type: "merge"` 付きで JSONL に追記。書き込み失敗時は stderr にエラー出力し例外を送出しない）
- [x] 1.3 `discover.py` のユニットテストに `load_merge_suppression` / `add_merge_suppression` のテストを追加。テストケース: 空ファイル、discover/merge 混在エントリ、重複キー、逆順入力の正規化
- [x] 1.4 `load_suppression_list()` に `type` フィルタを追加（`type: "merge"` エントリを除外し、`type` 未指定エントリのみ返す）+ regression テスト

## 2. merge_duplicates への suppression フィルタリング

- [x] 2.1 `prune.py` に `sys.path.insert(0, str(_plugin_root / "skills" / "discover" / "scripts"))` を追加し、`load_merge_suppression` をインポート。`merge_duplicates()` の**ループ外で1回だけ** `load_merge_suppression()` を呼び出してセットを取得し、ペアループ内の `.pin` / plugin チェック直後にセット照合で suppression チェックを追加（`skipped_suppressed` status で出力）
- [x] 2.2 `prune.py` のユニットテストに suppression 済みペアが `skipped_suppressed` になるテストを追加
- [x] 2.3 `prune.py` のユニットテストに suppression 未登録ペアが従来通り `proposed` になるテストを追加

## 3. evolve SKILL.md の merge 却下フロー明確化

- [x] 3.1 `skills/evolve/SKILL.md` の merge 却下時の指示に、具体的な Bash コマンドを明記:
  ```bash
  python3 -c "
  import sys; sys.path.insert(0, '<PLUGIN_DIR>/skills/discover/scripts')
  from discover import add_merge_suppression
  add_merge_suppression('skill-a', 'skill-b')
  "
  ```

## 4. 結合テスト・動作確認

- [x] 4.1 `merge_duplicates()` + suppression ファイルの結合テスト（suppressed ペアと非 suppressed ペアの混在ケース）
- [x] 4.2 discover 既存関数の regression テスト（`load_suppression_list()` が `type: "merge"` エントリを返さないことを確認）
- [x] 4.3 既存テストの回帰確認（`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v`）
