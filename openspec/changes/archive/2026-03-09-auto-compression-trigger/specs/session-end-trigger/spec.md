## MODIFIED Requirements

### Requirement: Evolve suggestion on session end
`session_summary.py` (Stop hook) のセッション終了処理に、`trigger_engine` を呼び出して evolve 実行条件を評価するステップを追加しなければならない (SHALL)。bloat トリガーを含む全トリガー条件を統合評価しなければならない (MUST)。

#### Scenario: Trigger condition met
- **WHEN** セッション終了時に `trigger_engine.evaluate_session_end()` が `triggered=True` を返す
- **THEN** `pending-trigger.json` にトリガー結果（reason、推奨コマンド、メッセージ）を書き出さなければならない (MUST)

#### Scenario: No trigger condition met
- **WHEN** セッション終了時に `trigger_engine.evaluate_session_end()` が `triggered=False` を返す
- **THEN** `pending-trigger.json` を書き出してはならない (MUST NOT)（サイレント）

#### Scenario: Trigger engine error
- **WHEN** `trigger_engine.evaluate_session_end()` が例外を発生させた
- **THEN** 例外をキャッチし、セッション終了処理を続行しなければならない (MUST)（サイレント失敗）

#### Scenario: Bloat trigger fires alongside other triggers
- **WHEN** session_count トリガーと bloat トリガーの両方が条件を満たす
- **THEN** 両方の reason を `all_reasons` に含め、bloat 情報もメッセージに追加しなければならない (MUST)

#### Scenario: Only bloat trigger fires
- **WHEN** bloat トリガーのみが条件を満たし、他のトリガー条件は未達
- **THEN** `TriggerResult(triggered=True, reason="bloat")` を返し、`/rl-anything:evolve` を推奨しなければならない (MUST)

#### Scenario: Bloat evaluation with project_dir
- **WHEN** session_summary.py が `CLAUDE_PROJECT_DIR` を受け取っている
- **THEN** `evaluate_session_end()` に `project_dir` を渡し、bloat_check() が正しいプロジェクトディレクトリを走査しなければならない (MUST)

#### Scenario: CLAUDE_PROJECT_DIR not set
- **WHEN** `CLAUDE_PROJECT_DIR` 環境変数が未設定（None または空文字列）
- **THEN** bloat 評価をスキップし、他のトリガー条件（session_count, days_elapsed, audit_overdue, corrections）のみ評価しなければならない (MUST)
