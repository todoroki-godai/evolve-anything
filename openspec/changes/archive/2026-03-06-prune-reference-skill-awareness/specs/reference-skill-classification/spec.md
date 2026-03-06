## ADDED Requirements

### Requirement: Reference skill type resolution
スキルのタイプ判定は以下の優先順位で解決しなければならない（MUST）:
1. SKILL.md の frontmatter に `type: reference` または `type: action` が明示されている場合はそれを使用
2. `evolve-state.json` の `skill_type_cache` にキャッシュがある場合はそれを使用
3. いずれもない場合は LLM サブエージェントでスキル内容から推定し、結果をキャッシュに保存

#### Scenario: Skill with type reference in frontmatter
- **WHEN** SKILL.md の frontmatter に `type: reference` が設定されている
- **THEN** `is_reference_skill()` が `True` を返す（LLM 呼び出しなし）

#### Scenario: Skill with type action in frontmatter
- **WHEN** SKILL.md の frontmatter に `type: action` が設定されている
- **THEN** `is_reference_skill()` が `False` を返す（LLM 呼び出しなし）

#### Scenario: Skill without type field and no cache
- **WHEN** SKILL.md の frontmatter に `type` フィールドがなく、キャッシュにもない
- **THEN** LLM サブエージェントでタイプを推定し、結果を `skill_type_cache` に保存して返す

#### Scenario: Skill without type field but cached
- **WHEN** SKILL.md の frontmatter に `type` フィールドがないが、キャッシュに推定結果がある
- **THEN** キャッシュの値を使用する（LLM 呼び出しなし）

#### Scenario: Frontmatter overrides cache
- **WHEN** frontmatter に `type: action` が設定されており、キャッシュには `reference` がある
- **THEN** frontmatter の `action` を優先する

#### Scenario: LLM inference failure
- **GIVEN** SKILL.md の frontmatter に `type` フィールドがなく、キャッシュにもない
- **WHEN** LLM サブエージェントによるタイプ推定が失敗する（タイムアウト、例外等）
- **THEN** `is_reference_skill()` は `False`（action 扱い）を返す（SHOULD）。エラーはログに記録する

#### Scenario: Cache invalidation by file modification
- **GIVEN** キャッシュにスキルの推定結果が保存されている
- **WHEN** スキルファイルの mtime がキャッシュ保存時より新しい
- **THEN** キャッシュを無効化し、再推定を行う

### Requirement: Reference skills excluded from zero invocation detection
`detect_zero_invocations()` は `type: reference` のスキルをゼロ呼び出し候補から除外しなければならない（MUST）。

#### Scenario: Reference skill with zero invocations
- **WHEN** `type: reference` のスキルが30日間呼び出しゼロである
- **THEN** `detect_zero_invocations()` の結果に含まれない

#### Scenario: Action skill with zero invocations
- **WHEN** `type` 未設定のスキルが30日間呼び出しゼロである
- **THEN** `detect_zero_invocations()` の結果に従来通り含まれる

### Requirement: Reference skill recommendation label
`suggest_recommendation()` は `type: reference` のスキルに対して `"keep推奨"` ラベルを返さなければならない（MUST）。ただしドリフト検出で候補になった場合は `"要確認"` を返す。

#### Scenario: Reference skill without drift
- **WHEN** `type: reference` のスキルがドリフト候補でない
- **THEN** `suggest_recommendation()` が `"keep推奨"` を返す

#### Scenario: Reference skill with drift detected
- **WHEN** `type: reference` のスキルがドリフト候補である
- **THEN** `suggest_recommendation()` が `"要確認"` を返す
