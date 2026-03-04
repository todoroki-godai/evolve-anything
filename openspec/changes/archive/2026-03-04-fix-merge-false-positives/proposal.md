## Why

コードベース全体に「本来の計算をせずダミー値/全件を黙って返す」スタブ・フォールバックが散在しており、機能が実質的に動作していない箇所が複数ある（GitHub Issue #3 起点）。

主な影響:
1. `semantic_similarity_check()` が全ペアを無条件返却 → 465件の誤検知 merge 提案（Issue #3）
2. `detect_contradictions()` が将来用スタブで常に空リスト → 矛盾検出が機能しない
3. `validate_corrections()` の LLM 失敗時フォールバックが全件 `is_learning=True` → reflect のフィルタが無効化
4. optimizer/rl-loop のスコアリングが複数箇所で黙って 0.5 を返す → 品質判定が無意味に
5. dry-run モードで固定ダミースコア → ユーザーが結果を誤解するリスク

## What Changes

- `semantic_similarity_check()` を TF-IDF + コサイン類似度による実質的な計算に置換（Issue #3 直接対応）
- reorganize の TF-IDF ロジックを共通ユーティリティ化
- `detect_contradictions()` を LLM ベースの実装に置換
- `validate_corrections()` フォールバックで `is_learning=False`（安全側）に変更
- optimizer の `_execution_evaluate()` / `_parse_cot_response()` フォールバック時に警告を出力
- dry-run スコアに `[dry-run]` マーカーを明示し、比較結果に注意文を付加
- `get_baseline_score()` の production パス LLM 失敗時にも stderr 警告を追加
- `_load_workflow_hints()` の stats-only JSON 時に stderr 警告を追加
- `backfill/analyze.py` の `semantic_validate()` dead code を削除（LLM を呼ばず戻り値も未使用）

## Capabilities

### New Capabilities
- `similarity-engine`: TF-IDF + コサイン類似度の共通計算エンジン
- `silent-fallback-safety`: スタブ/フォールバックの安全側デフォルトと警告出力の統一方針

### Modified Capabilities
- `merge`: duplicate_candidates が類似度閾値を超えたペアのみに限定される

## Impact

- **コード**: `audit.py`, `semantic_detector.py`, `optimize.py`, `run-loop.py`, `reorganize.py`, `backfill/analyze.py`
- **依存関係**: scikit-learn / scipy（既存依存、graceful degradation 維持）
- **テスト**: 各修正箇所にユニットテスト追加
- **互換性**: 出力スキーマ変更なし。フォールバックの挙動が「全許可」→「安全側拒否 + 警告」に変わる
