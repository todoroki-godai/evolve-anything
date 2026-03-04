## ADDED Requirements

### Requirement: Reorganize merge groups are filtered by pairwise similarity
`reorganize.merge_groups` からマージ候補ペアを展開する際、各ペアの TF-IDF コサイン類似度を計算し、閾値（デフォルト 0.60）未満のペアを除外する（MUST）。フィルタリングは `merge_duplicates()` 内で行う（MUST）。

フィルタリングロジックは `filter_merge_group_pairs(skills: List[str], skill_path_map: Dict[str, str], threshold: float) -> List[frozenset[str]]` として `scripts/lib/similarity.py` に実装する（MUST）。返り値は閾値以上のペアのリストであり、既存の `merge_duplicates()` 内の pairs 変数と同じ型とする。

#### Scenario: High similarity pair passes filter
- **WHEN** reorganize の merge_group に `["skill-a", "skill-b"]` が含まれ、両スキルの TF-IDF コサイン類似度が 0.81
- **THEN** 当該ペアはフィルタを通過し、マージ候補として `merge_proposals` に `status: "proposed"` で含まれる

#### Scenario: Low similarity pair filtered out
- **WHEN** reorganize の merge_group に `["skill-x", "skill-y"]` が含まれ、両スキルの TF-IDF コサイン類似度が 0.35
- **THEN** 当該ペアは `merge_proposals` に `status: "skipped_low_similarity"` で含まれる

#### Scenario: Large cluster with mixed similarity
- **WHEN** reorganize の merge_group に 7 スキルが含まれ（C(7,2)=21 ペア）、類似度 0.60 以上のペアが 2 ペアのみ
- **THEN** マージ候補は 2 件が `status: "proposed"` で含まれ、残り 19 ペアは `status: "skipped_low_similarity"` で含まれる

### Requirement: Filter threshold is configurable
reorganize 由来ペアのフィルタ閾値は `evolve-state.json` の `reorganize_merge_similarity_threshold` で設定可能とする（MUST）。未設定時のデフォルトは 0.60 とする（MUST）。

#### Scenario: Custom threshold from evolve-state.json
- **WHEN** `evolve-state.json` に `"reorganize_merge_similarity_threshold": 0.70` が設定されている
- **THEN** reorganize 由来ペアのフィルタ閾値として 0.70 が使用される

#### Scenario: Default threshold when not configured
- **WHEN** `evolve-state.json` に `reorganize_merge_similarity_threshold` が設定されていない
- **THEN** デフォルト値 0.60 が使用される

### Requirement: Graceful degradation without sklearn
sklearn が利用できない場合、reorganize 由来ペアのフィルタリングをスキップし、従来通り全ペアを展開する（MUST）。安全側に倒す（偽陽性は許容、マージ漏れは不可）。

#### Scenario: sklearn not available
- **WHEN** sklearn がインポートできない
- **THEN** reorganize 由来ペアはフィルタリングなしで全ペア展開され、既存の .pin / plugin / suppression チェックのみが適用される

### Requirement: Filter uses existing similarity engine
ペア単位の類似度計算は `scripts/lib/similarity.py` の共通エンジンを使用する（MUST）。独自の類似度計算を実装してはならない（MUST NOT）。

#### Scenario: Similarity calculated via shared engine
- **WHEN** reorganize 由来ペアのフィルタリングが実行される
- **THEN** `scripts/lib/similarity.py` の `compute_pairwise_similarity()` または `build_tfidf_matrix()` + cosine distance を使用してコサイン類似度を計算する
