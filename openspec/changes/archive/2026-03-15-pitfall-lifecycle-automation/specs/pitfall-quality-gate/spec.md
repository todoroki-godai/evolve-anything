## MODIFIED Requirements

### Requirement: Two-stage promotion gate
pitfall 記録時に Candidate → New の2段階昇格ゲートを適用する（SHALL）。初回エラーは `Status: Candidate` で仮記録し、同一根本原因が2回目に出現した場合にのみ `Status: New` に昇格する。corrections/errors からの自動検出結果も同じゲートを通過する。

#### Scenario: First occurrence creates Candidate
- **WHEN** 新しいエラーが初めて発生した
- **THEN** pitfalls.md に `Status: Candidate` で記録される。Pre-flight Check の対象にはならない

#### Scenario: Second occurrence promotes to New
- **WHEN** Candidate の根本原因と同一（Jaccard 類似度 ≥ 0.5）のエラーが再発した
- **THEN** `Status: New` に昇格し、`Last-seen` が更新される

#### Scenario: User correction bypasses gate
- **WHEN** ユーザーが直接訂正した（ユーザー訂正トリガー）
- **THEN** 品質ゲートをスキップし、即座に `Status: Active` で記録される

#### Scenario: Auto-detected duplicate increments occurrence
- **WHEN** corrections から抽出された pitfall が既存 Candidate と Jaccard ≥ ROOT_CAUSE_JACCARD_THRESHOLD
- **THEN** 新規 Candidate 作成せず Occurrence-count += 1
- **AND** count >= CANDIDATE_PROMOTION_COUNT（2）で New に自動昇格

#### Scenario: Auto-detected new correction passes through gate
- **WHEN** corrections.jsonl から自動抽出された pitfall パターンが既存 Candidate と重複しない
- **THEN** 通常の Candidate → New ゲートを通過する（自動検出は is_user_correction=False として処理）
