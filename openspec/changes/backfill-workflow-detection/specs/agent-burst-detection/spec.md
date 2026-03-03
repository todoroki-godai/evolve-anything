## ADDED Requirements

### Requirement: Skill/Team 外の連続 Agent 起動をワークフローとしてグルーピングしなければならない（MUST）
Skill ワークフローにも Team ワークフローにも属さない Agent 呼び出しが、直前の Agent から 5 分以内に連続して 2 回以上発生した場合、`workflow_type: "agent-burst"` のワークフローとして記録しなければならない（MUST）。

#### Scenario: 2 つの Agent が 5 分以内に連続
- **WHEN** Skill/Team 外で Agent(Explore, T=10:00) → Agent(general-purpose, T=10:03) が発生する
- **THEN** `workflows.jsonl` に `workflow_type: "agent-burst"` のレコードが1件追加される
- **AND** `step_count` は 2 である

#### Scenario: 5 分以上の gap で分割
- **WHEN** Agent(Explore, T=10:00) → Agent(general-purpose, T=10:06) が発生する（6 分間隔）
- **THEN** ワークフローは生成されない（各 Agent は ad-hoc 扱い）

#### Scenario: 単独の Agent は ad-hoc 扱い
- **WHEN** Skill/Team 外で Agent が 1 回だけ発生する
- **THEN** ワークフローは生成されない（ad-hoc Agent として従来どおり記録）

#### Scenario: 3 つの Agent で途中に gap
- **WHEN** Agent(T=10:00) → Agent(T=10:02) → Agent(T=10:09) が発生する
- **THEN** 最初の2つが1つの agent-burst ワークフローになる
- **AND** 3つ目は ad-hoc Agent として記録される

### Requirement: agent-burst の閾値は 5 分（300 秒）としなければならない（MUST）
連続 Agent 起動の同一ワークフロー判定に使用する timestamp 間隔の閾値は 300 秒としなければならない（MUST）。

#### Scenario: ちょうど 5 分の間隔
- **WHEN** Agent(T=10:00:00) → Agent(T=10:05:00) が発生する（ちょうど 300 秒）
- **THEN** 同一ワークフローとして記録される（閾値以内）
