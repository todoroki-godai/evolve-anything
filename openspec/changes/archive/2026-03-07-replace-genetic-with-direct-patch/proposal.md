## Why

現行の genetic-prompt-optimizer は世代ループ（mutate → evaluate → select）で 6〜15+ LLM コールを消費するが、bench 結果では long_skill の score 改善がほぼゼロ。corrections.jsonl には「何がどう悪いか」のテキスト情報が既にあるのに、fitness スカラー（0.0-1.0）に圧縮して遺伝的に探索し直すのは情報損失かつコスト過大。

## What Changes

- **BREAKING**: 遺伝的アルゴリズムの世代ループ（`run()` / `run_sectioned()`）を廃止
- 新パイプライン: corrections/sessions から エラー分類 → LLM 直接パッチ（1パス）
- corrections がない場合も LLM 1パス改善（「このスキルを改善して」）で統一
- bandit_selector / early_stopping / granularity / model_cascade / parallel は削除対象
- strategy_router を新ルーティング（corrections 有無判定）に置き換え
- 既存の accept/reject フロー（history.jsonl）は維持
- SKILL.md のインターフェース（`/optimize` コマンド）は維持

## Capabilities

### New Capabilities
- `direct-patch-optimizer`: corrections/sessions からエラーを分類し、LLM 1パスでスキルを直接パッチする最適化エンジン。corrections なしの場合は汎用 LLM 改善にフォールバック

### Modified Capabilities
- `optimize-accept-reject`: パイプライン変更に伴い、結果記録フォーマットに `strategy: "error_guided" | "llm_improve"` を追加

## Impact

- `skills/genetic-prompt-optimizer/scripts/` — optimize.py を大幅書き換え、補助モジュール 6 ファイル削除
- `skills/genetic-prompt-optimizer/tests/` — テストを新パイプラインに合わせて書き直し
- `skills/genetic-prompt-optimizer/SKILL.md` — `--generations`, `--population`, `--budget`, `--cascade`, `--parallel` オプション廃止
- `scripts/rl/fitness/` — fitness 関数は直接パッチでは不要だが、品質スコア参考表示用に維持
- `skills/rl-loop-orchestrator/SKILL.md` — 説明文更新（バリエーション生成 → 直接パッチ）
- `README.md`, `CLAUDE.md`, `docs/` — 遺伝的アルゴリズム関連記述の更新
- closes #19
