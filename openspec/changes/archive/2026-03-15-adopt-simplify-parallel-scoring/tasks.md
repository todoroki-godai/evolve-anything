## 1. rl-scorer 並列化

- [x] 1.1 `agents/rl-scorer.md` をオーケストレーター構成に書き換え（ドメイン推定 → 3サブエージェント並列起動 → 結果統合）
- [x] 1.2 technical-scorer のサブエージェント prompt を作成（技術品質5項目、model: haiku、JSON 出力）
- [x] 1.3 domain-scorer のサブエージェント prompt を作成（ドメイン推定結果を受け取り4項目評価、workflow_stats 補助シグナル対応）
- [x] 1.4 structural-scorer のサブエージェント prompt を作成（構造品質5項目 + プロジェクト規約チェック）
- [x] 1.5 サブエージェント失敗時のフォールバック処理を記述（失敗軸は 0.0、summary に記載）
- [x] 1.6 run-loop.py の `score_variant()` を並列スコアリング対応（ThreadPoolExecutor で3軸並列評価）
- [x] 1.7 `agents/rl-scorer.md` の frontmatter `model` を `haiku` に変更

## 2. 精度検証

- [x] 2.1 既存スキル3つ(reflect/audit/rl-loop)を tiered 並列で採点。全スキル0.70-0.75で妥当な評価
- [x] 2.2 フォールバック判断基準: σ>0.10 or 平均スコアが旧比-0.15以上乖離時にsonnetフォールバック。現状σ=0.047で基準内
- [x] 2.3 reflect×3回で分散テスト: 0.75/0.66/0.68 (平均0.697, σ=0.047)。許容範囲内

## 3. /simplify ゲート追加

- [x] 3.1 `skills/evolve/SKILL.md` の Step 5.5 後に /simplify 条件付き実行ステップ（Step 5.6）を追記
- [x] 3.2 条件分岐ロジックを記述（`fix_detail.changed_files` から Python ファイル判定 → 実行、Markdown のみ → スキップ、変更なし → スキップ）
- [x] 3.3 バージョン互換性チェック記述（/simplify 未対応環境ではスキップ）
- [x] 3.4 /simplify 結果のユーザー確認フロー記述（AskUserQuestion で適用/元に戻す）
- [x] 3.5 remediation.py の `record_outcome()` に `changed_files` フィールドを確実に含める（fix_detail 引数のドキュメント明確化）

## 4. レポート・ドキュメント更新

- [x] 4.1 evolve レポートに /simplify 実行結果セクションを追加
- [x] 4.2 CLAUDE.md の rl-scorer 関連記述を更新（並列アーキテクチャ反映）
- [x] 4.3 CHANGELOG.md に変更内容を追記
