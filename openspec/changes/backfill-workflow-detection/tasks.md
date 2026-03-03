## 1. team-driven ワークフロー検出

- [x] 1.1 `parse_transcript()` に TeamCreate/TeamDelete の状態追跡を追加（team_name, team 開始フラグ）
- [x] 1.2 TeamCreate〜TeamDelete 区間内の Agent を team ワークフローの step として記録
- [x] 1.3 team-driven ワークフローの `_finalize_workflow()` に `workflow_type` と `team_name` フィールドを追加

## 2. agent-burst ワークフロー検出

- [x] 2.1 Skill/Team 外の Agent を timestamp 付きバッファに蓄積するロジックを追加
- [x] 2.2 Agent 間の timestamp 間隔が 300 秒以内なら同一 burst、超えたら burst 確定のロジックを実装
- [x] 2.3 burst 確定時に `workflow_type: "agent-burst"` のワークフローレコードを生成（最小 2 Agent）

## 3. 既存 skill-driven との統合

- [x] 3.1 既存の skill-driven ワークフローレコードに `workflow_type: "skill-driven"` を追加
- [x] 3.2 サマリ出力に `workflows_by_type` の内訳を追加

## 4. テスト

- [x] 4.1 team-driven ワークフロー検出のテスト（TeamCreate→Agent→TeamDelete、TeamDelete なし、Agent なし）
- [x] 4.2 agent-burst 検出のテスト（2 Agent 連続、gap で分割、単独 ad-hoc、3 Agent 途中 gap、ちょうど 300 秒の境界）
- [x] 4.3 混在パターンのテスト（team 内 Skill→Agent、team 後に agent-burst）
- [x] 4.4 既存テストの `workflow_type` フィールド追加対応
