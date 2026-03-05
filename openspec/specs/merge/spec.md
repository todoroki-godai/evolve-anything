## Requirements

### Requirement: Merge integrates duplicate skills into one
Reorganize Phase の `merge_groups` と Prune Phase の `duplicate_candidates` の**和集合（重複排除済み）**に対して、2つのスキルを統合した1つの SKILL.md を生成する（MUST）。統合版はユーザー承認なしに適用してはならない（MUST NOT）。統合候補の JSON 出力は Python（prune.py）が行い、統合版 SKILL.md の生成は SKILL.md 経由で Claude に指示する（型A パターン）。

`duplicate_candidates` は `similarity_engine` によって類似度閾値（0.80）を超えたペアのみを含む（MUST）。全ペアの組み合わせを渡してはならない（MUST NOT）。

`reorganize.merge_groups` からのペア展開時は、`merge-group-filter` によるペア単位の類似度フィルタを適用する（MUST）。フィルタ閾値未満のペアは展開しない（MUST NOT）。フィルタで除外されたペアは `status: "skipped_low_similarity"` として `merge_proposals` に含める（MUST）。

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

#### Scenario: sklearn not installed (graceful degradation)
- **WHEN** `sklearn` がインポートできず `similarity_engine` が使用不能
- **THEN** `duplicate_candidates` は空リストとなり、merge は `reorganize.merge_groups` を従来通り全ペア展開で処理する（フィルタなし）

### Requirement: Merge preserves primary skill identity
Merge は使用回数（usage.jsonl の invocation count）が多い方を primary スキルとする（MUST）。統合版は primary スキルのパスに書き込み、secondary スキルをアーカイブする。

#### Scenario: Primary determined by usage count
- **WHEN** `skill-a` が 15 回使用、`skill-b` が 3 回使用
- **THEN** `skill-a` を primary とし、統合版を `skill-a/SKILL.md` に書き込む。`skill-b` をアーカイブする

#### Scenario: Equal usage count
- **WHEN** 両スキルの使用回数が同じ
- **THEN** ファイルパスの辞書順で先のものを primary とする

### Requirement: Merge respects existing protections
`.pin` ファイルが存在するスキルは merge 対象外とする（MUST）。plugin 由来スキルは merge 対象外とする（MUST）。

#### Scenario: Pinned skill excluded
- **WHEN** `duplicate_candidates` のペアの一方に `.pin` ファイルが存在する
- **THEN** 当該ペアは merge 対象から除外し、`skipped_pinned` として出力に含める

#### Scenario: Plugin skill excluded
- **WHEN** `duplicate_candidates` のペアの一方が `classify_artifact_origin() == "plugin"`
- **THEN** 当該ペアは merge 対象から除外する

#### Scenario: Suppressed pair is skipped
- **WHEN** `discover-suppression.jsonl` にペアの suppression エントリ（`type: "merge"`）が存在する
- **THEN** 当該ペアは `status: "skipped_suppressed"` として出力し、merge 対象から除外する

#### Scenario: Reorganize merge group false positives filtered
- **WHEN** reorganize の merge_group に 7 スキルが含まれ、ペア単位の類似度が 0.60 以上のペアが 1 件のみ
- **THEN** `merge_proposals` には当該 1 件が `status: "proposed"` で含まれ、残り 20 件は `status: "skipped_low_similarity"` で含まれる

### Requirement: Merge output structure
Merge の出力は以下の JSON 構造に従う（MUST）。

```json
{
  "merge_proposals": [
    {
      "primary": {"path": "string", "skill_name": "string", "usage_count": 0},
      "secondary": {"path": "string", "skill_name": "string", "usage_count": 0},
      "merged_content_preview": "first 10 lines of merged SKILL.md (status: proposed のみ。skipped_* / interactive_candidate では省略)",
      "similarity_score": 0.0,
      "status": "proposed" | "approved" | "rejected" | "skipped_pinned" | "skipped_plugin" | "skipped_suppressed" | "skipped_low_similarity" | "interactive_candidate"
    }
  ],
  "total_proposals": 0
}
```

`interactive_candidate` は reorganize 由来で merge 閾値未満かつ interactive 閾値以上のペアを示す。`similarity_score` は `interactive_candidate` では必須（MUST）、その他の status ではオプションとする。

#### Scenario: Dry-run output
- **WHEN** `--dry-run` フラグが設定されている
- **THEN** 全ての `merge_proposals` の status は `"proposed"` または `"interactive_candidate"` のままとなり、ファイル変更は行わない

#### Scenario: Interactive candidate in output
- **WHEN** reorganize 由来ペアの類似度が 0.48（interactive 閾値 0.40 以上、merge 閾値 0.60 未満）
- **THEN** `merge_proposals` に `status: "interactive_candidate"`, `similarity_score: 0.48` として含まれ、`merged_content_preview` は省略される
