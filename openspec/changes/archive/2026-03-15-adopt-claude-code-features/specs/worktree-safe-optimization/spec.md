## ADDED Requirements

### Requirement: Worktree isolation for optimize
optimize スキルの patch-apply-test サイクルを Agent tool（`isolation: "worktree"`）経由のサブエージェントに委譲し、隔離環境で試行する（MUST）。サブエージェント内で `optimize.py --apply-patch` を実行し、`pytest` でリグレッションチェックを行う（MUST）。

#### Scenario: patch in worktree
- **WHEN** optimize がスキルファイルに LLM パッチを適用する
- **THEN** Agent tool に `isolation: "worktree"` を指定してサブエージェントを起動する
- **AND** サブエージェント内で `optimize.py --apply-patch` を実行し、`pytest` でリグレッションチェックを行う
- **AND** パッチ適用結果（diff + テスト結果）をファイル出力する

#### Scenario: worktree cleanup on success
- **WHEN** worktree 内でのパッチ適用とテストが成功する
- **THEN** Agent tool が worktree パスとブランチ名を返却する
- **AND** 親コンテキストでユーザーにマージ判断を委ねる

#### Scenario: worktree cleanup on failure
- **WHEN** worktree 内でのテストが失敗する
- **THEN** Agent tool の自動クリーンアップにより worktree が削除される（変更なしと同等）
- **AND** メインの作業ディレクトリに影響しない
