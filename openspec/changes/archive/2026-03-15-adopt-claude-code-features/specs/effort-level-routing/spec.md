## ADDED Requirements

### Requirement: Phase-based effort guidance
evolve スキルの各フェーズ実行指示に、effort level に相当する自然言語ガイダンスを追加しなければならない（MUST）。

#### Scenario: Diagnose phase low effort
- **WHEN** evolve の Diagnose フェーズ（Step 1-3: データ収集・集計）が実行される
- **THEN** 「簡潔にデータを集計し、詳細な分析は不要」という指示が含まれる

#### Scenario: Compile phase standard effort
- **WHEN** evolve の Compile フェーズ（Step 4-7: 問題分類・修正）が実行される
- **THEN** 特別な effort 指示は含まれず、標準的な丁寧さで実行される

#### Scenario: Self-Evolution phase high effort
- **WHEN** evolve の Self-Evolution フェーズ（Step 8: パイプライン自己改善）が実行される
- **THEN** 「慎重に分析し、誤った calibration を避ける」という指示が含まれる

#### Scenario: Effort level effectiveness measurement
- **WHEN** effort level routing 導入から 2 週間が経過する
- **THEN** telemetry_query.py でフェーズ別トークン使用量を比較し、導入前後の差分を測定する（SHOULD）
- **AND** 有意な差がない場合は effort 指示を削除する
