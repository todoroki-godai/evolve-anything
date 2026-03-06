## ADDED Requirements

### Requirement: Detect reference drift by LLM semantic evaluation
`detect_reference_drift()` は参照型スキルの内容と現在のコードベース（CLAUDE.md、rules、関連ファイル）をサブエージェントで突合し、乖離度（0.0〜1.0）を評価しなければならない（MUST）。乖離度が閾値以上のスキルをドリフト候補として返す。閾値は `evolve-state.json` の `reference_drift_threshold` キーから読み込む（MUST）。未設定時のデフォルトは 0.5 とする。

#### Scenario: Skill content aligned with codebase
- **WHEN** 参照型スキルの内容が現在のコードベースと整合している
- **THEN** 乖離度が閾値未満となり、ドリフト候補に含まれない

#### Scenario: Skill content drifted from codebase
- **WHEN** 参照型スキルが参照する設計・設定・構造が現在のコードベースと乖離している
- **THEN** そのスキルがドリフト候補として返され、`drift_score` と `drift_reason`（乖離の説明）が含まれる

#### Scenario: Evaluation context gathering
- **WHEN** ドリフト評価を実行する
- **THEN** スキル内容から関連ファイル（CLAUDE.md、参照先 rules/skills、言及されるモジュール）を収集し、サブエージェントに渡す

### Requirement: Reference drift candidates in prune output
`run_prune()` の結果に `reference_drift_candidates` キーを含めなければならない（MUST）。このキーには `detect_reference_drift()` の結果を格納する。

#### Scenario: Prune output includes reference drift
- **WHEN** `run_prune()` を実行する
- **THEN** 結果辞書に `reference_drift_candidates` キーが存在し、リスト型である

### Requirement: Sub-agent evaluation failure handling
`detect_reference_drift()` のサブエージェント呼び出しが失敗した場合（タイムアウト、例外等）、そのスキルをドリフト候補に含めてはならない（MUST NOT）。エラーはログに記録する（SHOULD）。

#### Scenario: Sub-agent timeout
- **GIVEN** 参照型スキルのドリフト評価を実行中
- **WHEN** サブエージェント呼び出しがタイムアウトまたは例外で失敗する
- **THEN** そのスキルはドリフト候補に含まれず、エラーが記録される

### Requirement: Non-reference skills are not evaluated for drift
`detect_reference_drift()` は `type: reference` でないスキルを評価してはならない（MUST NOT）。

#### Scenario: Action skill is not evaluated
- **WHEN** `type` 未設定のスキルがプロジェクトに存在する
- **THEN** `detect_reference_drift()` の結果にそのスキルが含まれない
