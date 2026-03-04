## MODIFIED Requirements

### Requirement: Merge integrates duplicate skills into one
Reorganize Phase の `merge_groups` と Prune Phase の `duplicate_candidates` の**和集合（重複排除済み）**に対して、2つのスキルを統合した1つの SKILL.md を生成する（MUST）。統合版はユーザー承認なしに適用してはならない（MUST NOT）。統合候補の JSON 出力は Python（prune.py）が行い、統合版 SKILL.md の生成は SKILL.md 経由で Claude に指示する（型A パターン）。

`duplicate_candidates` は `similarity_engine` によって類似度閾値（0.80）を超えたペアのみを含む（MUST）。全ペアの組み合わせを渡してはならない（MUST NOT）。

`reorganize.merge_groups` からのペア展開時は、`merge-group-filter` によるペア単位の類似度フィルタを適用する（MUST）。フィルタ閾値未満のペアは展開しない（MUST NOT）。

#### Scenario: Two duplicate skills merged
- **WHEN** `duplicate_candidates` または `reorganize.merge_groups`（フィルタ通過後）に `{path_a: "skill-a/SKILL.md", path_b: "skill-b/SKILL.md"}` が含まれる
- **THEN** Merge は統合候補を JSON で出力し、SKILL.md の指示に従い Claude が両方の SKILL.md を読み込んで統合版を生成し、ユーザーに提示する

#### Scenario: Reorganize と Prune で同一ペアが検出された場合
- **WHEN** `reorganize.merge_groups` にフィルタ通過したペア `["skill-a", "skill-b"]` が含まれ、`duplicate_candidates` にも同じペアが含まれる
- **THEN** Merge は和集合の重複排除により当該ペアを1回のみ処理する

#### Scenario: User approves merge
- **WHEN** ユーザーが統合版を承認した
- **THEN** 統合版を primary スキル（使用回数が多い方）の SKILL.md に上書きし、secondary スキルを `archive_file()` でアーカイブする

#### Scenario: User rejects merge
- **WHEN** ユーザーが統合版を却下した
- **THEN** 両スキルは変更されず、当該ペアを `discover-suppression.jsonl` に追加して次回以降の提案を抑制する

#### Scenario: False positives eliminated by similarity threshold
- **WHEN** 30 スキルが存在し、`semantic_similarity_check()` が similarity_engine を使用する
- **THEN** `duplicate_candidates` には類似度 0.80 以上のペアのみが含まれ、無関係なスキルペア（例: `mailpit-test` と `refresh-aws-secrets`）は候補に含まれない

#### Scenario: Reorganize merge group false positives filtered
- **WHEN** reorganize の merge_group に 7 スキルが含まれ、ペア単位の類似度が 0.60 以上のペアが 1 件のみ
- **THEN** `merge_proposals` には当該 1 件が `status: "proposed"` で含まれ、残り 20 件は `status: "skipped_low_similarity"` で含まれる

#### Scenario: sklearn not installed (graceful degradation)
- **WHEN** `sklearn` がインポートできず `similarity_engine` が使用不能
- **THEN** `duplicate_candidates` は空リストとなり、merge は `reorganize.merge_groups` を従来通り全ペア展開で処理する（フィルタなし）
