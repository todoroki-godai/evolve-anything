## MODIFIED Requirements

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
