## MODIFIED Requirements

### Requirement: バックフィル結果のサマリを出力しなければならない（MUST）
処理完了後、抽出した Skill 呼び出し数・Agent 呼び出し数・エラー数・スキップしたセッション数・フィルタしたメッセージ数を JSON で出力しなければならない（MUST）。

#### Scenario: サマリ出力
- **WHEN** バックフィルが完了する
- **THEN** `{"sessions_processed": N, "skill_calls": N, "agent_calls": N, "errors": N, "skipped_sessions": N, "filtered_messages": N}` 形式の JSON が stdout に出力される
