## 1. Enrich Phase

- [x] 1.1 `skills/enrich/scripts/enrich.py` を作成: キーワードマッチ関数（Jaccard 係数 ≥ 0.15）で error/rejection/behavior パターンと既存スキルを照合するロジック。error_patterns / rejection_patterns が空の場合、behavior_patterns にフォールバック
- [x] 1.2 Enrich 出力構造（enrichments / unmatched_patterns / skipped_reason）の JSON 出力実装（LLM 呼び出しなし、型A パターン）
- [x] 1.3 `skills/evolve/SKILL.md` に Enrich Step 指示を追加: enrich.py の JSON 出力を読み取り、Claude が改善提案（diff 形式）をユーザーに対話的に提示する指示（最大3件制限）
- [x] 1.4 `enrich.py` のユニットテスト作成（キーワードマッチ・出力構造・plugin 除外・behavior フォールバック・全パターン空）

## 2. Merge サブステップ

- [x] 2.1 `prune.py` に `merge_duplicates()` 関数を追加: `reorganize.merge_groups` と `duplicate_candidates` の和集合（重複排除済み）から統合候補を JSON で出力（LLM 呼び出しなし、型A パターン）
- [x] 2.2 primary/secondary 判定ロジック（usage count ベース）の実装
- [x] 2.3 Merge 出力構造（merge_proposals）の実装と .pin / plugin 除外処理
- [x] 2.4 `skills/evolve/SKILL.md` に Merge Step 指示を追加: prune.py の merge JSON 出力を読み取り、Claude が統合版 SKILL.md を生成してユーザーに承認を求める指示
- [x] 2.5 `prune.py` のユニットテスト追加（merge 関連: primary 判定・pin 保護・plugin 除外・Reorganize 重複排除）

## 3. Reorganize Phase

- [x] 3.1 `skills/reorganize/scripts/reorganize.py` を作成: TF-IDF ベクトル生成 + scipy 階層クラスタリング
- [x] 3.2 クラスタ結果から merge_groups（2+スキル）と split_candidates（300行超）の検出ロジック
- [x] 3.3 scipy 未インストール時の graceful degradation 実装（初回実行時に `pip install scipy scikit-learn` を案内）
- [x] 3.4 スキル数 < 5 のスキップ判定と configurable 距離閾値（evolve-state.json）の実装
- [x] 3.5 `reorganize.py` のユニットテスト作成（クラスタリング・スキップ判定・graceful degradation）

## 4. evolve.py 統合

- [x] 4.1 `evolve.py` に Enrich Phase を追加（Discover の直後、Optimize の前）
- [x] 4.2 `evolve.py` に Reorganize Phase を追加（Optimize の後、Prune の前）— Reorganize の `merge_groups` を Prune に渡す
- [x] 4.3 `evolve.py` の sys.path に enrich / reorganize を追加
- [x] 4.4 dry-run 時の Enrich / Merge / Reorganize 動作確認

## 5. ドキュメント更新

- [x] 5.1 `skills/evolve/SKILL.md` を更新: パイプライン順序を `Discover → Enrich → Optimize → Reorganize → Prune(+Merge) → Fitness Evolution → Report` に更新
- [x] 5.2 CHANGELOG.md にエントリ追加 + plugin.json のバージョン更新
