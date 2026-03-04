## Context

コードベースに「計算スタブ」「黙って 0.5 を返すフォールバック」が散在し、複数機能が実質的に動作していない。Issue #3 の merge 誤検知が最も深刻だが、同じパターンが semantic_detector, optimizer, rl-loop にも存在する。

## Goals / Non-Goals

**Goals:**
- 全てのスタブ/ダミーフォールバックを実装または安全側デフォルトに置換
- フォールバック発動時にユーザーが気づける警告を出力
- 30 スキル環境で merge_proposals が適正件数に収まることを検証

**Non-Goals:**
- LLM ベースの高精度な意味理解（TF-IDF で実用上十分）
- reorganize のクラスタリングロジック自体の変更
- 出力スキーマの変更

## Decisions

### D1: TF-IDF ロジックの共通化

**選択: `scripts/lib/similarity.py` に共通モジュールを新設**

`reorganize.py` から `build_tfidf_matrix()` を抽出し、ペアワイズ類似度計算関数を追加。`reorganize.py` と `audit.py` の両方からインポート。

### D2: `semantic_similarity_check()` の置換

**選択: TF-IDF + コサイン類似度でフィルタ**

ファイル全文を読み込み → TF-IDF 行列構築 → ペアワイズ コサイン類似度計算 → 閾値（0.80）以上のペアのみ返却。sklearn 未インストール時は空リスト（graceful degradation）。

### D3: `detect_contradictions()` の実装

**選択: `claude -p` で矛盾ペアを検出**

`semantic_analyze()` と同じパターンで `claude -p` を呼び出し、corrections リスト内の矛盾ペアを検出。LLM 失敗時は空リスト（安全側 = 矛盾なしとして扱う）。

### D4: `validate_corrections()` フォールバックの安全側変更

**選択: フォールバック時は `is_learning=False` + 警告出力**

現状の `is_learning=True`（全件学習対象）を `is_learning=False`（全件除外）に変更。理由: 誤って非学習データを reflect に流すより、見逃す方が安全。再実行すれば拾える。

### D5: optimizer スコアリングフォールバックの明示化

**選択: フォールバック発動時に stderr 警告を出力**

`_execution_evaluate()` の test-tasks 未設定時と `_parse_cot_response()` のパース失敗時に stderr へ警告メッセージを出力。スコア値自体は 0.5 のまま（中立値として合理的）だが、ユーザーにフォールバックであることを明示。戻り値の型は float のまま変更しない。

### D6: dry-run スコアの明示化

**選択: dry-run スコアの summary に `[dry-run]` を付与し、比較時に注意文を出力**

`get_baseline_score()` と `score_variant()` の dry-run 結果に明確なマーカーを付与。既に summary に `[dry-run]` はあるが、`score_variant()` の戻り値（float のみ）では判別不能なため、呼び出し元で dry-run 時の注意を表示。

### D7: backfill/analyze.py の dead code 削除

**選択: `semantic_validate()` 関数と呼び出し元を削除**

`semantic_validate()` は LLM を呼ばず prompt テンプレートを返すだけで、さらに `run_analysis()` で戻り値を `format_report()` に渡しておらず完全な dead code。`semantic_detector.py` の `validate_corrections()` が同等の役割を担うため、混乱の元となるコードを削除する。

### D8: get_baseline_score() production fallback 警告

**選択: production パスの LLM 失敗時にも stderr 警告を追加**

dry-run だけでなく production パスでも LLM 失敗時に 0.50 を返す。`summary` に失敗メッセージはあるが呼び出し元で目立たないため、stderr に明示的な警告を追加する。

### D9: _load_workflow_hints() silent empty return 警告

**選択: stats-only JSON 時に stderr 警告を追加**

stats-only JSON（ワークフローヒントなし）の場合に `""` を黙って返し mutation 品質が劣化する。stderr に警告を出力し、呼び出し元でヒント不在を認識できるようにする。

## Risks / Trade-offs

- **[Risk] validate_corrections フォールバック変更で有効な correction を見逃す** → 再実行で回復可能。現状の「全件通過」より安全
- **[Risk] detect_contradictions の LLM 呼び出しコスト** → reflect 実行時のみ発動。correction 件数は通常 10 件未満
- **[Risk] TF-IDF の精度限界** → 閾値 0.80 で保守的にフィルタ。false negative は reorganize のクラスタリングで補完
- **[Risk] optimizer 警告が多すぎてノイズになる** → test-tasks 未設定時は初回のみ表示（1ラン1回）
