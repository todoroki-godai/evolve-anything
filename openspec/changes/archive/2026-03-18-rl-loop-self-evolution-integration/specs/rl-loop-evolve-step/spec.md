## ADDED Requirements

### Requirement: --evolve フラグによる自己進化パターン組み込み制御

`run-loop.py` は `--evolve` CLI フラグを受け付けなければならない（MUST）。デフォルトは無効（false）。有効時、各ループの最終ステップ（Step 5.5）として自己進化パターン組み込みを実行する。

#### Scenario: --evolve フラグなしで実行
- **WHEN** `--evolve` フラグなしで rl-loop を実行する
- **THEN** 従来通りテキスト最適化ループのみ実行し、自己進化パターン組み込みは行わない

#### Scenario: --evolve フラグありで実行
- **WHEN** `--evolve` フラグありで rl-loop を実行する
- **THEN** 各ループの Step 5 後に自己進化適性判定 → パターン組み込み提案を実行する

### Requirement: 単一スキル向け自己進化の共通関数

`skill_evolve.py` に以下の2関数を追加しなければならない（MUST）:

1. **`assess_single_skill(skill_name, skill_dir)`** — 1スキルの適性判定結果を返す
2. **`apply_evolve_proposal(proposal)`** — `evolve_skill_proposal()` の返り値を受け取り、SKILL.md セクション追記 + `references/pitfalls.md` 作成 + バックアップ作成を実行する

独立コマンド（evolve-skill）、rl-loop、remediation の3箇所から呼び出される。

#### Scenario: 適性 medium 以上
- **WHEN** 対象スキルの適性が medium または high と判定される
- **THEN** suitability="medium" or "high" を含む結果を返す

#### Scenario: 適性 low または rejected
- **WHEN** 対象スキルの適性が low または rejected と判定される
- **THEN** suitability="low" or "rejected" を含む結果を返す

#### Scenario: 既に自己進化済み
- **WHEN** 対象スキルが既に自己進化パターンを持っている（`is_self_evolved_skill()` が true）
- **THEN** suitability="already_evolved" を含む結果を返す

#### Scenario: apply_evolve_proposal で正常適用
- **WHEN** 有効な proposal を `apply_evolve_proposal()` に渡す
- **THEN** SKILL.md にセクションが追加され、`references/pitfalls.md` が作成され、バックアップ `.md.pre-evolve-backup` が作成される
- **AND** `{"applied": True, "backup_path": "...", "error": None}` を返す

#### Scenario: apply_evolve_proposal で references/ ディレクトリ自動作成
- **WHEN** `references/` ディレクトリが存在しない状態で `apply_evolve_proposal()` を呼び出す
- **THEN** `references/` ディレクトリが自動作成され、`pitfalls.md` が配置される

#### Scenario: apply_evolve_proposal でバックアップ作成
- **WHEN** SKILL.md が存在する状態で `apply_evolve_proposal()` を呼び出す
- **THEN** 変更前の SKILL.md が `.md.pre-evolve-backup` として保存される

### Requirement: 自己進化パターン組み込み実行

適性判定で medium 以上かつ人間確認で承認された場合、`evolve_skill_proposal()` を呼び出してテンプレートを生成し、`apply_evolve_proposal()` で SKILL.md にセクション追加 + `references/pitfalls.md` を作成しなければならない（MUST）。

#### Scenario: 承認された場合のパターン組み込み
- **WHEN** 自己進化パターン組み込みが承認される
- **THEN** SKILL.md に Pre-flight Check / Self-Update Rules / Failure-triggered Learning / Pitfall Lifecycle Management / Success Patterns セクションが追加される
- **AND** `references/pitfalls.md` がテンプレートから作成される

#### Scenario: 却下された場合
- **WHEN** 自己進化パターン組み込みが却下される
- **THEN** SKILL.md と references/ に変更を加えない

#### Scenario: --auto モードでの自動承認
- **WHEN** `--auto` フラグが有効な状態で適性 medium 以上と判定される
- **THEN** 人間確認をスキップし、自動的にパターン組み込みを実行する

#### Scenario: --dry-run モードでの実行
- **WHEN** `--dry-run` フラグが有効な状態で `--evolve` も有効
- **THEN** 適性判定結果を表示するが、SKILL.md やファイルへの変更は行わない

### Requirement: 結果の記録

自己進化パターン組み込みの結果をループ結果（`loop_result`）に含めなければならない（MUST）。

#### Scenario: evolve 結果がループ結果に含まれる
- **WHEN** `--evolve` 有効でループが完了する
- **THEN** `loop_result` に `evolve_suitability`（適性レベル）、`evolve_applied`（適用有無）、`evolve_scores`（5軸スコア）フィールドが含まれる

#### Scenario: evolve 無効時の結果
- **WHEN** `--evolve` 無効でループが完了する
- **THEN** `loop_result` に evolve 関連フィールドは含まれない
