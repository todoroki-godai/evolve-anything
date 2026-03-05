## MODIFIED Requirements

### Requirement: Reorganize merge groups are filtered by pairwise similarity
`reorganize.merge_groups` からマージ候補ペアを展開する際、各ペアの TF-IDF コサイン類似度を計算し、閾値（デフォルト 0.60）未満のペアを除外する（MUST）。フィルタリングは `merge_duplicates()` 内で行う（MUST）。

加えて、interactive 閾値（デフォルト 0.40）以上かつ merge 閾値未満のペアを `interactive` リストとして返却する（MUST）。

フィルタリングロジックは `filter_merge_group_pairs(skills: List[str], skill_path_map: Dict[str, str], threshold: float, interactive_threshold: float = 0.40) -> tuple[List[frozenset[str]], List[tuple[frozenset[str], float]]]` として `scripts/lib/similarity.py` に実装する（MUST）。返り値は `(passed, interactive)` のタプルとし、`passed` は閾値以上のペアのリスト、`interactive` は interactive 閾値以上かつ merge 閾値未満のペアと類似度スコアのタプルリストとする。

#### Scenario: High similarity pair passes filter
- **WHEN** reorganize の merge_group に `["skill-a", "skill-b"]` が含まれ、両スキルの TF-IDF コサイン類似度が 0.81
- **THEN** 当該ペアは `passed` リストに含まれる

#### Scenario: Medium similarity pair becomes interactive
- **WHEN** reorganize の merge_group に `["skill-c", "skill-d"]` が含まれ、両スキルの TF-IDF コサイン類似度が 0.52
- **THEN** 当該ペアは `interactive` リストに `(frozenset(["skill-c", "skill-d"]), 0.52)` として含まれる

#### Scenario: Low similarity pair filtered out
- **WHEN** reorganize の merge_group に `["skill-x", "skill-y"]` が含まれ、両スキルの TF-IDF コサイン類似度が 0.25
- **THEN** 当該ペアは `passed` にも `interactive` にも含まれない

### Requirement: Interactive threshold is configurable
interactive 閾値は `evolve-state.json` の `interactive_merge_similarity_threshold` で設定可能とする（MUST）。未設定時のデフォルトは 0.40 とする（MUST）。

#### Scenario: Custom interactive threshold
- **WHEN** `evolve-state.json` に `"interactive_merge_similarity_threshold": 0.45` が設定されている
- **THEN** interactive 判定に 0.45 が使用される

#### Scenario: Default interactive threshold
- **WHEN** `evolve-state.json` に `interactive_merge_similarity_threshold` が設定されていない
- **THEN** デフォルト値 0.40 が使用される

### Requirement: Graceful degradation without sklearn preserves interactive contract
sklearn が利用できない場合、`filter_merge_group_pairs()` は `passed` に全ペアを返し、`interactive` は空リストを返す（MUST）。interactive 候補の判定は sklearn 依存のため、graceful degradation 時には提案しない。

#### Scenario: sklearn not available returns empty interactive list
- **WHEN** sklearn がインポートできない
- **THEN** `filter_merge_group_pairs()` は `(all_pairs, [])` を返す。`all_pairs` は全ペアの frozenset リスト、`interactive` は空リスト
