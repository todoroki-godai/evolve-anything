## ADDED Requirements

### Requirement: Interactive merge proposal for medium-similarity pairs
reorganize の `merge_groups` で検出され、merge フィルタ閾値（0.60）未満かつ interactive 閾値（デフォルト 0.40）以上のペアに対して、`merge_proposals` に `status: "interactive_candidate"` として出力する（MUST）。SKILL.md（evolve）側で AskUserQuestion を使って統合を対話的に提案する（MUST）。

#### Scenario: Medium similarity pair becomes interactive candidate
- **WHEN** reorganize の merge_group に `["skill-a", "skill-b"]` が含まれ、ペア類似度が 0.52（interactive 閾値 0.40 以上、merge 閾値 0.60 未満）
- **THEN** `merge_proposals` に `status: "interactive_candidate"` として含まれ、evolve の Step 5 で AskUserQuestion により統合提案される

#### Scenario: Low similarity pair remains skipped
- **WHEN** reorganize の merge_group に `["skill-x", "skill-y"]` が含まれ、ペア類似度が 0.25（interactive 閾値 0.40 未満）
- **THEN** `merge_proposals` に `status: "skipped_low_similarity"` として含まれる（従来動作）

#### Scenario: User approves interactive merge
- **WHEN** ユーザーが `interactive_candidate` のペアの統合を承認した
- **THEN** Claude が両スキルの SKILL.md を読み込んで統合版を生成し、primary の SKILL.md に上書き、secondary を `archive_file()` でアーカイブする

#### Scenario: User rejects interactive merge
- **WHEN** ユーザーが `interactive_candidate` のペアの統合を却下した
- **THEN** 当該ペアを `add_merge_suppression()` で merge suppression に登録し、次回以降の提案を抑制する

### Requirement: Interactive proposal limit per evolve run
1回の evolve 実行で提案する `interactive_candidate` は最大3件とする（MUST）。類似度が高い順に優先する（MUST）。

#### Scenario: More than 3 interactive candidates
- **WHEN** `interactive_candidate` が 5 件存在する
- **THEN** 類似度上位3件のみが evolve の Step 5 で対話提案され、残り2件は次回以降に持ち越す

### Requirement: Interactive candidates respect existing protections
`interactive_candidate` も既存の pin/plugin/suppression チェックを通過したペアのみが対象となる（MUST）。`merge_duplicates()` 内で pin/plugin/suppression チェックが先に適用され、除外されたペアは `interactive_candidate` にならない。

#### Scenario: Suppressed pair excluded from interactive candidates
- **WHEN** reorganize 由来ペアの類似度が 0.50 だが、当該ペアが `discover-suppression.jsonl` に `type: "merge"` で登録されている
- **THEN** 当該ペアは `status: "skipped_suppressed"` となり、`interactive_candidate` にはならない

#### Scenario: Pinned skill excluded from interactive candidates
- **WHEN** reorganize 由来ペアの類似度が 0.45 だが、一方のスキルに `.pin` ファイルが存在する
- **THEN** 当該ペアは `status: "skipped_pinned"` となり、`interactive_candidate` にはならない

### Requirement: Overflow interactive candidates are implicitly carried over
1回の evolve で提案上限（3件）を超えた `interactive_candidate` は、明示的な持ち越し管理を行わない（MUST NOT）。次回 evolve 実行時に再度 `merge_duplicates()` が計算し、条件を満たせば再び `interactive_candidate` として検出される。

#### Scenario: Unpresented candidates reappear next run
- **WHEN** 前回 evolve で 5件の `interactive_candidate` のうち上位3件のみ提案され、残り2件が未提案
- **THEN** 次回 evolve 実行時に `merge_duplicates()` が再計算し、条件を満たすペアは再度 `interactive_candidate` として出力される

### Requirement: Interactive candidate output includes similarity score
`interactive_candidate` の `merge_proposals` エントリには `similarity_score` フィールドを含める（MUST）。これにより SKILL.md 側で類似度に基づく優先順位付けが可能になる。

#### Scenario: Similarity score in output
- **WHEN** ペア `["skill-a", "skill-b"]` が `interactive_candidate` として出力される
- **THEN** エントリに `"similarity_score": 0.52` が含まれる
