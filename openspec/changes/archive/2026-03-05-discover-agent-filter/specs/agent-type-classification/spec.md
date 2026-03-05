## ADDED Requirements

### Requirement: Built-in Agent type identification
`classify_agent_type(agent_name)` は Agent 名を受け取り、MUST `"builtin"` / `"custom_global"` / `"custom_project"` のいずれかを返す。判定は既知の組み込み名セット（`BUILTIN_AGENT_NAMES`）とカスタム Agent ディレクトリ走査の併用で行う。`BUILTIN_AGENT_NAMES` は `scripts/lib/agent_classifier.py` に定義し、他モジュールから参照する。

#### Scenario: Built-in Agent detected by known list
- **WHEN** Agent 名が `Explore`, `Plan`, `general-purpose` 等の `BUILTIN_AGENT_NAMES` に含まれる
- **THEN** `classify_agent_type()` は MUST `"builtin"` を返す

#### Scenario: Custom global Agent detected
- **WHEN** Agent 名が `BUILTIN_AGENT_NAMES` に含まれず、`~/.claude/agents/<name>.md` が存在する
- **THEN** `classify_agent_type()` は MUST `"custom_global"` を返す

#### Scenario: Custom project Agent detected
- **WHEN** Agent 名が `BUILTIN_AGENT_NAMES` に含まれず、`.claude/agents/<name>.md`（プロジェクトルート）が存在する
- **THEN** `classify_agent_type()` は MUST `"custom_project"` を返す

#### Scenario: Agent exists in both global and project directories
- **WHEN** Agent 名が `BUILTIN_AGENT_NAMES` に含まれず、`~/.claude/agents/<name>.md` と `.claude/agents/<name>.md` の両方が存在する
- **THEN** `classify_agent_type()` は MUST `"custom_project"` を返す（project 優先）

#### Scenario: Unknown Agent defaults to builtin
- **WHEN** Agent 名が `BUILTIN_AGENT_NAMES` に含まれず、カスタム Agent ディレクトリにも存在しない
- **THEN** `classify_agent_type()` は MUST `"builtin"` を返す（安全側にフォールバック）

#### Scenario: Agent directory does not exist
- **WHEN** `~/.claude/agents/` および `.claude/agents/` のいずれも存在しない
- **THEN** `classify_agent_type()` は MUST 例外を発生させず、`BUILTIN_AGENT_NAMES` のみで判定する。リストに含まれない Agent は `"builtin"` を返す

#### Scenario: I/O error during directory scan
- **WHEN** Agent ディレクトリの走査中に PermissionError 等の I/O エラーが発生する
- **THEN** `classify_agent_type()` は MUST 該当ディレクトリをスキップし、残りの情報で判定を継続する。エラーを握り潰さず WARNING レベルでログ出力する

### Requirement: Built-in Agent filtering in discover
`detect_behavior_patterns()` は MUST 組み込み Agent をメインランキングから除外し、`agent_usage_summary` として別途集計する。

#### Scenario: Built-in Agent excluded from main ranking
- **WHEN** `Agent:Explore` が usage.jsonl に 76 回記録されている
- **THEN** メインランキングの patterns に MUST 含まれず、`agent_usage_summary` に含まれる

#### Scenario: Custom Agent remains in main ranking
- **WHEN** `Agent:my-custom-agent` が `.claude/agents/my-custom-agent.md` で定義されており、usage.jsonl に 10 回記録されている
- **THEN** メインランキングの patterns に MUST `suggestion: "skill_candidate"` として含まれる

### Requirement: agent_usage_summary output format
`agent_usage_summary` は MUST `plugin_summary` と同様の構造で patterns リスト末尾に追加する。

#### Scenario: agent_usage_summary structure
- **WHEN** 組み込み Agent の利用記録が存在する
- **THEN** patterns に MUST `{"type": "agent_usage_summary", "pattern": "builtin_agent_usage", "count": <total>, "suggestion": "info_only", "agent_breakdown": {...}}` が追加される

#### Scenario: agent_breakdown internal schema
- **WHEN** `Agent:Explore` が 76 回使用され、サブカテゴリとして spec-review(27), debug(24) を持つ
- **THEN** `agent_breakdown` は MUST `{"Agent:Explore": {"count": 76, "subcategories": [{"category": "spec-review", "count": 27}, {"category": "debug", "count": 24}]}}` の構造を持つ

#### Scenario: Subcategories preserved in summary
- **WHEN** `Agent:Explore` が spec-review(27), debug(24) のサブカテゴリを持つ
- **THEN** `agent_breakdown` 内の `Agent:Explore` エントリに MUST `subcategories` が含まれる
