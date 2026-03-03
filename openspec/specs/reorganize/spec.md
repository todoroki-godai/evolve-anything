## ADDED Requirements

### Requirement: Reorganize clusters skills by content similarity
Reorganize Phase は全スキルの SKILL.md テキストから TF-IDF ベクトルを生成し、階層クラスタリングで意味的に近いスキル群を特定する（MUST）。plugin 由来スキルは分析対象外とする（MUST）。

#### Scenario: Related skills clustered together
- **WHEN** `aws-cdk-deploy`, `aws-cdk-development`, `aws-common` の3スキルが存在する
- **THEN** Reorganize はこれらを同一クラスタに分類し、「統合候補」として提案する

#### Scenario: Unrelated skills in separate clusters
- **WHEN** `mailpit-test` と `draw-io` が存在する
- **THEN** Reorganize はこれらを別クラスタに分類する

### Requirement: Reorganize proposes merge for clusters with 2+ skills
クラスタ内のスキル数が 2 以上の場合、「統合候補グループ」として提案する（MUST）。提案はユーザーへの情報提供のみであり、自動統合は行わない（MUST NOT）。

#### Scenario: Cluster with 3 related skills
- **WHEN** クラスタ `["openspec-propose", "openspec-refine", "openspec-apply-change"]` が検出された（ただし全て plugin 由来でないと仮定）
- **THEN** 出力の `merge_groups` に当該グループを含め、「これらのスキルは統合を検討してください」と提案する

#### Scenario: All clusters are singletons
- **WHEN** 全スキルが異なるクラスタに分類された
- **THEN** `merge_groups` は空リストとなる

### Requirement: Reorganize detects oversized skills for split
単一スキルで SKILL.md の行数が 300 行を超える場合、「分割候補」として提案する（MUST）。

#### Scenario: Oversized skill detected
- **WHEN** `mega-skill/SKILL.md` が 450 行ある
- **THEN** 出力の `split_candidates` に当該スキルを含め、分割を提案する

#### Scenario: All skills within size limit
- **WHEN** 全スキルの SKILL.md が 300 行以下
- **THEN** `split_candidates` は空リストとなる

### Requirement: Reorganize skips when skill count is low
分析対象のスキル数が 5 未満の場合、Reorganize Phase をスキップする（MUST）。

#### Scenario: Less than 5 skills
- **WHEN** 分析対象のスキルが 3 個
- **THEN** Reorganize は `{"skipped": true, "reason": "insufficient_skills", "count": 3}` を出力し、クラスタリングを実行しない

### Requirement: Reorganize graceful degradation without scipy
scipy がインストールされていない環境では、Reorganize Phase をスキップし警告を出力する（MUST）。エラーで evolve 全体を停止してはならない（MUST NOT）。

#### Scenario: scipy not installed
- **WHEN** `import scipy` が ImportError を返す
- **THEN** Reorganize は `{"skipped": true, "reason": "scipy_not_available"}` を出力する

### Requirement: Reorganize output structure
Reorganize Phase の出力は以下の JSON 構造に従う（MUST）。

```json
{
  "skipped": false,
  "clusters": [
    {
      "cluster_id": 0,
      "skills": ["skill-a", "skill-b", "skill-c"],
      "centroid_keywords": ["deploy", "aws", "cdk"]
    }
  ],
  "merge_groups": [
    {
      "skills": ["skill-a", "skill-b"],
      "reason": "high content similarity",
      "similarity_score": 0.85
    }
  ],
  "split_candidates": [
    {
      "skill_name": "mega-skill",
      "line_count": 450,
      "threshold": 300
    }
  ],
  "total_clusters": 0,
  "total_merge_groups": 0,
  "total_split_candidates": 0
}
```

#### Scenario: Full output with all sections populated
- **WHEN** 10 スキルが分析され、3 クラスタに分類、1 統合候補グループ、1 分割候補がある
- **THEN** 出力は `clusters` 3件、`merge_groups` 1件、`split_candidates` 1件を含む

### Requirement: Reorganize uses configurable distance threshold
クラスタリングの距離閾値はデフォルト 0.7 とし、`evolve-state.json` の `reorganize_threshold` で変更可能とする（MUST）。

#### Scenario: Custom threshold configured
- **WHEN** `evolve-state.json` に `"reorganize_threshold": 0.5` が設定されている
- **THEN** Reorganize は距離閾値 0.5 でクラスタリングを実行する
