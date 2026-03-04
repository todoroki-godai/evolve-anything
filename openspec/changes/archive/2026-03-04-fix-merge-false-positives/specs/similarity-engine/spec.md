## ADDED Requirements

### Requirement: Similarity engine computes pairwise cosine similarity
`scripts/lib/similarity.py` は TF-IDF ベクトルからペアワイズのコサイン類似度を計算し、閾値以上のペアのみを返す機能を提供する（MUST）。

#### Scenario: Two similar skills detected
- **WHEN** `skill-a` と `skill-b` のコサイン類似度が 0.85 で閾値が 0.80
- **THEN** `compute_pairwise_similarity()` は `[{"path_a": "skill-a", "path_b": "skill-b", "similarity": 0.85}]` を返す

#### Scenario: Unrelated skills filtered out
- **WHEN** `mailpit-test` と `refresh-aws-secrets` のコサイン類似度が 0.12 で閾値が 0.80
- **THEN** `compute_pairwise_similarity()` の結果にこのペアは含まれない

#### Scenario: Large number of skills
- **WHEN** 30 スキルが入力され、真に類似するペアが 3 組のみ
- **THEN** 結果は 3 件以下のペアを返す（C(30,2)=435 件ではない）

### Requirement: Similarity engine provides shared TF-IDF builder
`build_tfidf_matrix()` を共通関数として提供する（MUST）。reorganize と audit/prune の両方がこの関数を使用する。

#### Scenario: Build TF-IDF from skill texts
- **WHEN** `{"skill-a": "deploy AWS CDK stack", "skill-b": "test email with mailpit"}` が入力される
- **THEN** TF-IDF 行列、特徴量名、スキル名のタプルを返す

#### Scenario: sklearn not installed
- **WHEN** `sklearn` がインポートできない
- **THEN** `(None, None, None)` を返す

### Requirement: Similarity engine reads artifact file content
ペアワイズ類似度計算時、各アーティファクトの**ファイル全文**を読み込んで TF-IDF の入力とする（MUST）。パスのみの比較であってはならない（MUST NOT）。

#### Scenario: Full content comparison
- **WHEN** `skills/aws-cdk-deploy/SKILL.md`（200行）と `skills/aws-cdk-development/SKILL.md`（180行）のパスが渡される
- **THEN** 両ファイルの全テキストを読み込み、TF-IDF ベクトルを構築して類似度を計算する

#### Scenario: File read failure
- **WHEN** `skills/broken/SKILL.md` が存在しないか読み取れない
- **THEN** 当該ファイルをスキップし、stderr に `"Warning: failed to read skills/broken/SKILL.md, skipping"` を出力する

#### Scenario: Empty input (zero skills)
- **WHEN** 空の辞書 `{}` が `compute_pairwise_similarity()` に渡される
- **THEN** 空リスト `[]` を返す

#### Scenario: Single skill input
- **WHEN** スキルが 1 件のみの辞書が渡される
- **THEN** ペアが存在しないため空リスト `[]` を返す

#### Scenario: Custom threshold
- **WHEN** `compute_pairwise_similarity(paths, threshold=0.90)` と閾値が指定される
- **THEN** コサイン類似度 0.90 以上のペアのみを返す（デフォルトは 0.80）
