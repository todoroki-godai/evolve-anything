## ADDED Requirements

### Requirement: Enrich matches patterns to existing skills
Enrich Phase は Discover が検出した error_patterns、rejection_patterns、および behavior_patterns を既存スキル群と照合し、関連するスキルを特定する（MUST）。照合にはキーワードマッチ（Jaccard 係数 ≥ 0.15）を使用する。plugin 由来スキルは照合対象外とする（MUST）。error_patterns / rejection_patterns が空の場合、behavior_patterns の ad-hoc パターンからも照合を試みる（SHOULD）。

#### Scenario: Error pattern matches existing skill
- **WHEN** Discover が `error_pattern: "cdk deploy failed: stack timeout"` を検出し、既存スキルに `aws-cdk-deploy`（SKILL.md 内に "cdk" "deploy" キーワードあり）が存在する
- **THEN** Enrich は `aws-cdk-deploy` を関連スキルとして特定し、`matched_skills` リストに含める

#### Scenario: Behavior pattern matches existing skill (fallback)
- **WHEN** errors.jsonl / history.jsonl が未生成で error_patterns / rejection_patterns が空であり、Discover が `behavior_pattern: "Agent:Explore が 477 回使用"` を検出し、既存スキルに Explore 関連のスキルが存在する
- **THEN** Enrich は behavior_patterns からの照合にフォールバックし、関連スキルを `matched_skills` リストに含める

#### Scenario: All pattern sources empty
- **WHEN** errors.jsonl / history.jsonl が未生成で、かつ usage.jsonl にも behavior_patterns が検出されない
- **THEN** Enrich は `{"enrichments": [], "unmatched_patterns": [], "total_enrichments": 0, "total_unmatched": 0, "skipped_reason": "no_patterns_available"}` を出力し、後続フェーズに制御を渡す

#### Scenario: No matching skill found
- **WHEN** Discover が `error_pattern: "docker compose timeout"` を検出し、既存スキルに docker 関連のものが存在しない
- **THEN** Enrich は当該パターンを `unmatched_patterns` リストに含め、Discover の従来フロー（新規候補）に戻す

#### Scenario: Plugin skill excluded from matching
- **WHEN** Discover が `rejection_pattern: "openspec format error"` を検出し、`openspec-propose` スキルが存在するが origin が "plugin"
- **THEN** Enrich は `openspec-propose` を照合対象外とし、当該パターンを `unmatched_patterns` に含める

### Requirement: Enrich generates improvement proposals for matched skills
Enrich Phase は照合された (パターン, スキル) ペアに対して、LLM を使用して改善提案（diff 形式）を生成する（MUST）。改善提案はユーザー承認なしに適用してはならない（MUST NOT）。

#### Scenario: Improvement proposal generated
- **WHEN** error_pattern `"SMTP connection refused"` がスキル `mailpit-test` に照合された
- **THEN** Enrich は LLM に当該パターンとスキル内容を渡し、「SMTP 接続エラー時のトラブルシューティング手順を追加」のような改善提案を生成する

#### Scenario: Dry-run mode
- **WHEN** `--dry-run` フラグが設定されている
- **THEN** Enrich は改善提案を生成して出力するが、ファイルの変更は行わない

### Requirement: Enrich output structure
Enrich Phase の出力は以下の JSON 構造に従う（MUST）。

```json
{
  "enrichments": [
    {
      "pattern_type": "error" | "rejection" | "behavior",
      "pattern": "string",
      "matched_skill": "skill_name",
      "skill_path": "path/to/SKILL.md",
      "proposal": "improvement description"
    }
  ],
  "unmatched_patterns": [
    {
      "pattern_type": "error" | "rejection" | "behavior",
      "pattern": "string",
      "suggestion": "skill_candidate" | "rule_candidate"
    }
  ],
  "total_enrichments": 0,
  "total_unmatched": 0
}
```

#### Scenario: Output includes both matched and unmatched
- **WHEN** Discover が 3 error_patterns を検出し、2つが既存スキルに照合され、1つが照合されなかった
- **THEN** 出力の `enrichments` は 2 件、`unmatched_patterns` は 1 件、`total_enrichments` は 2、`total_unmatched` は 1 となる

### Requirement: Enrich limits LLM calls
Enrich Phase はキーワードマッチで候補を最大3件に絞った上で LLM 呼び出しを行う（MUST）。4件以上の候補がある場合は Jaccard 係数の上位3件のみを処理する。

#### Scenario: More than 3 candidates
- **WHEN** 5つの error_patterns が既存スキルに照合された
- **THEN** Jaccard 係数の上位3件のみ LLM に送信し、残り2件は `skipped_low_relevance` として出力に含める
