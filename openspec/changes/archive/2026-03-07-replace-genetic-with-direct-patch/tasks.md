## 1. コンテキスト収集モジュール

- [x] 1.1 `_collect_corrections()` 実装: corrections.jsonl から対象スキル関連の pending レコードを抽出（直近10件制限）
- [x] 1.2 `_collect_context()` 実装: workflow_stats, audit collect_issues, pitfalls.md を統合してコンテキスト辞書を返す
- [x] 1.3 テスト: corrections あり/なし/大量ケースの collect 動作

## 2. プロンプト構築

- [x] 2.1 `_build_patch_prompt()` 実装: error_guided モード用プロンプト（corrections のエラー分類 + パッチ指示）
- [x] 2.2 `_build_patch_prompt()` 実装: llm_improve モード用プロンプト（usage 統計 + audit issues + 汎用改善指示）
- [x] 2.3 プロンプト品質検証: 実際のスキル + corrections で手動テストし、パッチの質を確認
- [x] 2.4 テスト: 両モードのプロンプト生成が期待通りの構造を持つこと

## 3. DirectPatchOptimizer コア

- [x] 3.1 `DirectPatchOptimizer` クラス実装: `__init__`, `run()`, `_call_llm()`, `_regression_gate()`（既存流用）
- [x] 3.2 `--mode auto|error_guided|llm_improve` オプション追加
- [x] 3.3 error_guided 指定で corrections 0件時の llm_improve フォールバック実装
- [x] 3.4 テスト: run() の正常系（error_guided / llm_improve）、regression gate 不合格ケース

## 4. CLI・履歴インターフェース

- [x] 4.1 optimize.py の argparse を新オプション体系に更新（廃止オプションのエラーメッセージ含む）
- [x] 4.2 history.jsonl に `strategy` / `corrections_used` フィールド追加
- [x] 4.3 `--accept` / `--reject` / `--restore` / `--dry-run` の既存フローを維持確認
- [x] 4.4 テスト: 廃止オプション使用時のエラーメッセージ、history.jsonl フォーマット

## 5. 旧モジュール削除・SKILL.md 更新

- [x] 5.1 strategy_router.py, granularity.py, bandit_selector.py, early_stopping.py, model_cascade.py, parallel.py を削除
- [x] 5.2 対応するテストファイル（test_strategy_router.py 等 6ファイル）を削除
- [x] 5.3 optimize.py から旧モジュールの import と `GeneticOptimizer` / `Individual` の旧ロジックを除去
- [x] 5.4 SKILL.md を更新: 廃止オプション削除、新 `--mode` オプション追加、説明文を直接パッチモードに変更
- [x] 5.5 rl-loop-orchestrator/SKILL.md の説明更新（3バリエーション生成 → 直接パッチ、API コスト目安更新）
- [x] 5.6 evolve/SKILL.md の GA オプション参照確認・削除

## 6. 統合テスト・検証

- [x] 6.1 test_integration.py を新パイプラインに合わせて書き直し
- [x] 6.2 全テスト実行: `python3 -m pytest skills/genetic-prompt-optimizer/ -v`
- [x] 6.3 実スキルで `/optimize` を手動実行し、accept/reject フローの動作確認

## 7. ドキュメント更新

- [x] 7.1 README.md の遺伝的アルゴリズム記述を直接パッチに更新
- [x] 7.2 CLAUDE.md の「遺伝的最適化」記述を更新
- [x] 7.3 docs/evolve/optimize.md を直接パッチモードに書き換え
- [x] 7.4 CHANGELOG.md に変更エントリ追加（コミット時）
