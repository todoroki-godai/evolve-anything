## 1. 共通類似度エンジンの作成

- [x] 1.1 `scripts/lib/similarity.py` を新設し、`build_tfidf_matrix()` を `reorganize.py` から移植
- [x] 1.2 `compute_pairwise_similarity(paths, threshold)` を実装（TF-IDF + コサイン類似度、閾値フィルタ、デフォルト threshold=0.80）
- [x] 1.3 sklearn 未インストール時の graceful degradation（空リスト返却）を実装
- [x] 1.4 ファイル読取失敗時のスキップ + stderr 警告を実装
- [x] 1.5 空入力（0件/1件）時に空リスト返却を実装

## 2. semantic_similarity_check の置換

- [x] 2.1 `audit.py` の `semantic_similarity_check()` を `similarity.py` の `compute_pairwise_similarity()` を呼ぶ実装に置換
- [x] 2.2 戻り値のフォーマットを既存の `merge_duplicates()` の入力と互換に保つ（`path_a`, `path_b`, `similarity` キー）

## 3. reorganize.py のリファクタ

- [x] 3.1 `reorganize.py` の `build_tfidf_matrix()` を `scripts/lib/similarity.py` からの import に置換
- [x] 3.2 reorganize の既存テストが通ることを確認

## 4. detect_contradictions の実装

- [x] 4.1 `semantic_detector.py` の `detect_contradictions()` を `claude -p` ベースの実装に置換
- [x] 4.2 LLM 失敗時は空リスト + stderr 警告のフォールバックを実装
- [x] 4.3 空入力ガード（0件/1件以下で LLM 呼び出し不要、即座に空リスト返却）を実装

## 4b. reflect.py からの detect_contradictions 呼び出し追加

- [x] 4b.1 `reflect.py` の corrections 処理フローに `detect_contradictions()` の呼び出しを追加
- [x] 4b.2 矛盾ペアが検出された場合にユーザーへ警告を表示する処理を追加

## 5. validate_corrections フォールバックの安全側変更

- [x] 5.1 `semantic_detector.py` の `validate_corrections()` フォールバックを `is_learning=False` に変更
- [x] 5.2 `semantic_analyze()` 内のカウント不一致・例外時フォールバックも同様に `is_learning=False` に変更
- [x] 5.3 フォールバック発動時の stderr 警告メッセージを追加（カウント不一致: `"Warning: validate_corrections count mismatch (expected N, got M), defaulting to is_learning=False"`）

## 6. optimizer スコアリングフォールバックの警告追加

- [x] 6.1 `optimize.py` の `_execution_evaluate()` で test-tasks 未設定時に stderr 警告を出力
- [x] 6.2 `optimize.py` の `_parse_cot_response()` でパース失敗時に stderr 警告を出力
- [x] 6.3 `optimize.py` の `_load_workflow_hints()` で stats-only JSON 時に stderr 警告 `"Warning: no workflow hints found in stats-only data"` を出力

## 7. dry-run スコアの明示化

- [x] 7.1 `run-loop.py` の `score_variant()` dry-run 結果表示時に注意文を出力
- [x] 7.2 バリエーション比較結果の表示箇所で dry-run 注意文を追加
- [x] 7.3 `run-loop.py` の `get_baseline_score()` production パスで LLM 失敗時に stderr 警告 `"Warning: baseline scoring failed, defaulting to 0.50"` を出力

## 8. backfill/analyze.py dead code 削除

- [x] 8.1 `backfill/analyze.py` の `semantic_validate()` 関数を削除
- [x] 8.2 `run_analysis()` からの `semantic_validate()` 呼び出しを削除
- [x] 8.3 既存テストが通ることを確認

## 9. テスト

- [x] 9.1 `scripts/tests/test_similarity.py` を新設し、類似スキル検出・非類似フィルタ・エッジケースのテストを追加
- [x] 9.2 `semantic_detector.py` のフォールバック変更・矛盾検出のテストを追加
- [x] 9.3 optimizer の警告出力テストを追加
- [x] 9.4 全テスト実行（`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v`）で回帰なしを確認
