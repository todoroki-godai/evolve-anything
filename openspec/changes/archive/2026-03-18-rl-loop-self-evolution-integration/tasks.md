## 1. 共通関数の追加

- [x] 1.1 `skill_evolve.py` に `assess_single_skill(skill_name, skill_dir, *, project=None)` + `apply_evolve_proposal(proposal)` の2関数を追加（テスト先行）
- [x] 1.2 ユニットテスト作成:
  - `assess_single_skill`: 適性 high/medium/low/rejected/already_evolved の各ケース
  - `apply_evolve_proposal`: 正常適用、references/ ディレクトリ自動作成、バックアップ `.md.pre-evolve-backup` 作成
- [x] 1.3 `remediation.py:fix_skill_evolve()` を `apply_evolve_proposal()` 呼び出しにリファクタ（既存テスト維持）

## 2. 独立コマンド `/rl-anything:evolve-skill`

- [x] 2.1 `skills/evolve-skill/SKILL.md` 作成（スキル定義 + トリガーワード + 使用例）
- [x] 2.2 SKILL.md にスキル名/ファイルパス解決、適性判定結果表示、組み込み承認フロー、--dry-run 対応を記述
- [x] 2.3 CLAUDE.md のコンポーネントテーブルに evolve-skill エントリを追加

## 3. rl-loop `--evolve` フラグ統合

- [x] 3.1 `run-loop.py` に `--evolve` CLI 引数を追加
- [x] 3.2 Step 5.5 として `_try_evolve_skill()` 関数を実装（`assess_single_skill` 呼び出し → 適性判定 → `apply_evolve_proposal` → 人間確認）
- [x] 3.3 `loop_result` に `evolve_suitability`/`evolve_applied`/`evolve_scores` フィールドを追加
- [x] 3.4 `--evolve` + `--dry-run` / `--auto` の組み合わせ動作テスト
- [x] 3.5 `skills/rl-loop-orchestrator/SKILL.md` に `--evolve` オプション説明を追記

## 4. テスト・検証

- [x] 4.1 `assess_single_skill` + `apply_evolve_proposal` + `_try_evolve_skill` の統合テスト（mock 使用で LLM 呼び出し回避）
- [x] 4.2 `--evolve --dry-run` で既存 rl-loop テストが壊れないことを確認
- [x] 4.3 既に自己進化済みスキルに対する実行でスキップされることを確認
