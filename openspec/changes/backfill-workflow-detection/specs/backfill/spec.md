## MODIFIED Requirements

### Requirement: バックフィル結果のサマリを出力しなければならない（MUST）
処理完了後、抽出した Skill 呼び出し数・Agent 呼び出し数・エラー数・スキップしたセッション数・フィルタしたメッセージ数・ワークフロー数（タイプ別内訳）を JSON で出力しなければならない（MUST）。

#### Scenario: サマリ出力
- **WHEN** バックフィルが完了する
- **THEN** `{"sessions_processed": N, "skill_calls": N, "agent_calls": N, "errors": N, "skipped_sessions": N, "filtered_messages": N, "workflows": N, "workflows_by_type": {"skill-driven": N, "team-driven": N, "agent-burst": N}}` 形式の JSON が stdout に出力される

## ADDED Requirements

### Requirement: workflows.jsonl のレコードに workflow_type を含めなければならない（MUST）
新規作成する全てのワークフローレコードに `workflow_type` フィールド（`"skill-driven"` / `"team-driven"` / `"agent-burst"`）を含めなければならない（MUST）。

#### Scenario: skill-driven ワークフローの workflow_type
- **WHEN** Skill→Agent パターンでワークフローが検出される
- **THEN** レコードの `workflow_type` は `"skill-driven"` である

#### Scenario: 既存レコードの後方互換
- **WHEN** `workflow_type` フィールドがないレコードを読み取る
- **THEN** `"skill-driven"` として扱う
