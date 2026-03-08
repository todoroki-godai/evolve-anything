## ADDED Requirements

### Requirement: orphan_rule の修正提案を生成する
generate_proposals() は `orphan_rule` issue に対して、ルールファイルの削除提案を生成しなければならない（MUST）。

#### Scenario: orphan_rule の削除提案
- **WHEN** `orphan_rule` issue が proposable に分類されている
- **THEN** `{"issue": ..., "proposal": "ルール「{name}」の削除", "rationale": "...参照されていません..."}` が返される

### Requirement: stale_memory の修正提案を生成する
generate_proposals() は `stale_memory` issue に対して、エントリの更新または削除の提案を生成しなければならない（MUST）。

#### Scenario: stale_memory の更新/削除提案
- **WHEN** `stale_memory` issue が proposable に分類されている
- **THEN** `{"issue": ..., "proposal": "MEMORY.md の「{path}」エントリの更新/削除", "rationale": "...実体が見つかりません..."}` が返される

### Requirement: memory_duplicate の統合提案を生成する
generate_proposals() は `memory_duplicate` issue に対して、セクション統合の提案を生成しなければならない（MUST）。

#### Scenario: memory_duplicate の統合提案
- **WHEN** `memory_duplicate` issue が proposable に分類されている
- **THEN** `{"issue": ..., "proposal": "セクション「{a}」と「{b}」の統合", "rationale": "...類似度: {similarity}..."}` が返される

### Requirement: hooks_unconfigured の手動対応レポートを生成する
generate_proposals() は `hooks_unconfigured` issue を manual_required として処理し、設定手順の説明テキストを返さなければならない（MUST）。

#### Scenario: hooks_unconfigured のレポート
- **WHEN** `hooks_unconfigured` issue が manual_required に分類されている
- **THEN** proposal ではなく description テキスト（「hooks 設定が見つかりません。`/rl-anything:update` で設定を追加してください。」）が返される
