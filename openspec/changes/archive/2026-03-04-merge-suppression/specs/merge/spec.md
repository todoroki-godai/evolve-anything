## MODIFIED Requirements

### Requirement: Merge integrates duplicate skills into one
Reorganize Phase の `merge_groups` と Prune Phase の `duplicate_candidates` の**和集合（重複排除済み）**に対して、2つのスキルを統合した1つの SKILL.md を生成する（MUST）。統合版はユーザー承認なしに適用してはならない（MUST NOT）。統合候補の JSON 出力は Python（prune.py）が行い、統合版 SKILL.md の生成は SKILL.md 経由で Claude に指示する（型A パターン）。**suppression 登録済みのペアは `skipped_suppressed` として除外する（MUST）。**

#### Scenario: Two duplicate skills merged
- **WHEN** `duplicate_candidates` または `reorganize.merge_groups` に `{path_a: "skill-a/SKILL.md", path_b: "skill-b/SKILL.md"}` が含まれる
- **THEN** Merge は統合候補を JSON で出力し、SKILL.md の指示に従い Claude が両方の SKILL.md を読み込んで統合版を生成し、ユーザーに提示する

#### Scenario: Reorganize と Prune で同一ペアが検出された場合
- **WHEN** `reorganize.merge_groups` に `["skill-a", "skill-b"]` が含まれ、`duplicate_candidates` にも同じペアが含まれる
- **THEN** Merge は和集合の重複排除により当該ペアを1回のみ処理する

#### Scenario: User approves merge
- **WHEN** ユーザーが統合版を承認した
- **THEN** 統合版を primary スキル（使用回数が多い方）の SKILL.md に上書きし、secondary スキルを `archive_file()` でアーカイブする

#### Scenario: User rejects merge
- **WHEN** ユーザーが統合版を却下した
- **THEN** 両スキルは変更されず、当該ペアを `discover-suppression.jsonl` に `{"pattern": "skill-a::skill-b", "type": "merge"}` 形式で追加して次回以降の提案を抑制する

#### Scenario: Suppressed pair is skipped
- **WHEN** `discover-suppression.jsonl` にペアの suppression エントリが存在する
- **THEN** 当該ペアは `status: "skipped_suppressed"` として出力し、merge 対象から除外する

### Requirement: Merge output structure
Merge の出力は以下の JSON 構造に従う（MUST）。

```json
{
  "merge_proposals": [
    {
      "primary": {"path": "string", "skill_name": "string", "usage_count": 0},
      "secondary": {"path": "string", "skill_name": "string", "usage_count": 0},
      "merged_content_preview": "first 10 lines of merged SKILL.md (status: proposed のみ。skipped_* では省略)",
      "status": "proposed" | "approved" | "rejected" | "skipped_pinned" | "skipped_plugin" | "skipped_suppressed"
    }
  ],
  "total_proposals": 0
}
```

#### Scenario: Dry-run output
- **WHEN** `--dry-run` フラグが設定されている
- **THEN** 全ての `merge_proposals` の status は `"proposed"` のままとなり、ファイル変更は行わない
