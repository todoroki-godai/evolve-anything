## MODIFIED Requirements

### Requirement: optimize Step 3 で accept/reject を確認する

最適化結果を表示した後、AskUserQuestion ツールで accept/reject をユーザーに確認し、結果を `optimize.py` CLI で `history.jsonl` に記録する（MUST）。`--dry-run` または `--restore` の場合は最適化ループを実行しないため、accept/reject 確認をスキップする（MUST）。

#### Scenario: ユーザーが結果を accept する場合

- **WHEN** optimize の最適化が完了し、結果サマリが表示された後（`--dry-run` および `--restore` を除く）
- **THEN** AskUserQuestion で「この最適化結果を採用しますか？」と確認する（options: 「Accept（結果を採用）」「Reject（却下して理由を記録）」）。ユーザーが「Accept」を選択した場合、`python3 <PLUGIN_DIR>/skills/genetic-prompt-optimizer/scripts/optimize.py --target <TARGET> --accept` を実行して `history.jsonl` に `human_accepted: true` を記録する

#### Scenario: ユーザーが結果を reject する場合

- **WHEN** optimize の最適化が完了し、結果サマリが表示された後（`--dry-run` および `--restore` を除く）
- **THEN** AskUserQuestion で「この最適化結果を採用しますか？」と確認する（options: 「Accept（結果を採用）」「Reject（却下して理由を記録）」）。ユーザーが「Reject」を選択した場合、却下理由を AskUserQuestion（open-ended）で確認し、`python3 <PLUGIN_DIR>/skills/genetic-prompt-optimizer/scripts/optimize.py --target <TARGET> --reject --reason "<理由>"` を実行して `history.jsonl` に `human_accepted: false` と `rejection_reason` を記録する

#### Scenario: `--dry-run` または `--restore` の場合

- **WHEN** optimize が `--dry-run`（構造テスト）または `--restore`（バックアップ復元）で実行された場合
- **THEN** accept/reject 確認をスキップし、`history.jsonl` への `human_accepted` 記録は行わない

#### Scenario: history.jsonl に strategy フィールドを記録する

- **WHEN** accept または reject が記録される
- **THEN** `history.jsonl` のエントリに `strategy` フィールド（`"error_guided"` または `"llm_improve"`）と `corrections_used`（使用した corrections 件数。`llm_improve` 時は `0`）を含める
