## ADDED Requirements

### Requirement: TeamCreate から TeamDelete までの Agent 起動をワークフローとして記録しなければならない（MUST）
トランスクリプト内で TeamCreate ツール呼び出しを検出した場合、対応する TeamDelete（またはトランスクリプト終了）までの Agent 呼び出しを1つのワークフローとして `workflows.jsonl` に記録しなければならない（MUST）。

#### Scenario: TeamCreate→Agent→Agent→TeamDelete のワークフロー検出
- **WHEN** トランスクリプトに TeamCreate → Agent(Explore) → Agent(general-purpose) → TeamDelete の順で tool_use が存在する
- **THEN** `workflows.jsonl` に `workflow_type: "team-driven"` のレコードが1件追加される
- **AND** `step_count` は 2 である
- **AND** `team_name` に TeamCreate の `team_name` パラメータが記録される

#### Scenario: TeamDelete がないまま トランスクリプトが終了
- **WHEN** TeamCreate の後に TeamDelete がなくトランスクリプトが終了する
- **THEN** トランスクリプト終了時点でワークフローを確定する

#### Scenario: Team 内で Agent が起動されない
- **WHEN** TeamCreate → TeamDelete の間に Agent 呼び出しがない
- **THEN** ワークフローレコードは生成されない（step_count=0 のレコードは作らない）

### Requirement: Team 区間内の Skill→Agent は team-driven ワークフローのステップとして記録しなければならない（MUST）
TeamCreate〜TeamDelete の区間内で Skill → Agent が発生した場合、その Agent は team-driven ワークフローのステップとして記録しなければならない（MUST）。別の skill-driven ワークフローを生成してはならない。

#### Scenario: Team 内での Skill→Agent 発生
- **WHEN** TeamCreate → Skill → Agent(Explore) → TeamDelete の順で tool_use が存在する
- **THEN** Agent(Explore) は team-driven ワークフローのステップとして記録される
- **AND** skill-driven ワークフローは生成されない

### Requirement: team-driven ワークフローの team_name を記録しなければならない（MUST）
`workflows.jsonl` のレコードに `team_name` フィールドを含め、TeamCreate で指定されたチーム名を記録しなければならない（MUST）。

#### Scenario: team_name の記録
- **WHEN** TeamCreate の input に `{"team_name": "impl-team"}` が含まれる
- **THEN** ワークフローレコードの `team_name` は `"impl-team"` である
