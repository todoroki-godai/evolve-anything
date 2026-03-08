## ADDED Requirements

### Requirement: Coverage スコアを算出する
`score_coverage()` は、環境の各レイヤー（CLAUDE.md / Rules / Skills / Memory / Hooks）に最低限の定義が存在するかをチェックし、0.0〜1.0 のスコアを返さなければならない（MUST）。

#### Scenario: 全レイヤーが揃っている環境
- **WHEN** CLAUDE.md が存在し、Rules が 1 つ以上、Skills が 1 つ以上、Memory が存在し、Hooks が設定されており、CLAUDE.md に Skills セクションがある
- **THEN** coverage スコアは 1.0 を返す

#### Scenario: Hooks が未設定の環境
- **WHEN** CLAUDE.md / Rules / Skills / Memory はすべて存在するが、`.claude/settings.json` に hooks 設定がない
- **THEN** coverage スコアは 1.0 未満を返す（Hooks チェック項目が fail）

#### Scenario: 最小環境（CLAUDE.md のみ）
- **WHEN** CLAUDE.md のみ存在し、Rules / Skills / Memory / Hooks がすべて存在しない
- **THEN** coverage スコアは 0.2 以下を返す

### Requirement: Consistency スコアを算出する
`score_consistency()` は、レイヤー間の矛盾や断絶がないかをチェックし、0.0〜1.0 のスコアを返さなければならない（MUST）。

#### Scenario: CLAUDE.md で言及された Skill がすべて実在する
- **WHEN** CLAUDE.md に skill-a, skill-b が記載されており、両方とも `.claude/skills/` 配下に実在する
- **THEN** Skill 実在チェックは pass

#### Scenario: CLAUDE.md で言及された Skill が存在しない
- **WHEN** CLAUDE.md に skill-x が記載されているが `.claude/skills/skill-x/` が存在しない
- **THEN** Skill 実在チェックは fail し、consistency スコアが減少する

#### Scenario: MEMORY.md 内のファイルパス参照が実在する
- **WHEN** MEMORY.md 内で言及されているファイルパス（`scripts/`, `skills/` 等）がすべてプロジェクト内に実在する
- **THEN** Memory パス存在チェックは pass

#### Scenario: MEMORY.md 内のファイルパス参照が実在しない
- **WHEN** MEMORY.md 内で `scripts/lib/obsolete.py` が言及されているが、そのファイルが存在しない
- **THEN** Memory パス存在チェックは fail し、consistency スコアが減少する

#### Scenario: トリガーワードの重複がない
- **WHEN** すべての Skill のトリガーワードが互いに重複していない
- **THEN** トリガー重複チェックは pass

### Requirement: Completeness スコアを算出する
`score_completeness()` は、定義されたものが実際に動くレベルで完成しているかをチェックし、0.0〜1.0 のスコアを返さなければならない（MUST）。

#### Scenario: 全 Skill が必須セクションを含み、Rules が制約を遵守
- **WHEN** すべての Skill が 50 行以上で必須セクション（Usage, Steps）を含み、すべての Rule が 3 行以内で、CLAUDE.md が 200 行以内で、ハードコード値がない
- **THEN** completeness スコアは 1.0 を返す

#### Scenario: 空の Skill が存在する
- **WHEN** skill-empty が 10 行しかなく、必須セクションが欠けている
- **THEN** completeness スコアが減少する

### Requirement: Efficiency スコアを算出する
`score_efficiency()` は、冗長さや肥大化がないかをチェックし、0.0〜1.0 のスコアを返さなければならない（MUST）。

#### Scenario: 重複 Skill がなく、near-limit もない環境
- **WHEN** 意味的重複 Skill がなく、80% 超えの near-limit がなく、未使用 Skill がなく、孤立 Rule がない
- **THEN** efficiency スコアは 1.0 を返す

#### Scenario: 未使用 Skill が存在する
- **WHEN** 30 日以上ゼロ invoke の Skill が 2 つ存在する（全 10 Skill 中）
- **THEN** efficiency スコアが減少する

#### Scenario: usage.jsonl が存在しない場合のフォールバック
- **WHEN** `usage.jsonl` が存在しない（テレメトリデータなし）
- **THEN** 未使用 Skill チェックは skip し、残りのチェック項目（意味的重複 Skill、near-limit、孤立 Rule）のみでスコアを按分算出する

### Requirement: Coherence Score を統合算出する
`compute_coherence_score()` は、4軸のスコアを重み付き平均で統合し、overall スコアと各軸の詳細を返さなければならない（MUST）。重みは Coverage 0.25、Consistency 0.30、Completeness 0.25、Efficiency 0.20 とする。

#### Scenario: 全軸が 1.0 の場合
- **WHEN** coverage=1.0, consistency=1.0, completeness=1.0, efficiency=1.0
- **THEN** overall は 1.0 を返す

#### Scenario: Consistency のみ低い場合
- **WHEN** coverage=1.0, consistency=0.5, completeness=1.0, efficiency=1.0
- **THEN** overall は 0.25*1.0 + 0.30*0.5 + 0.25*1.0 + 0.20*1.0 = 0.85 を返す

#### Scenario: 戻り値に各軸の詳細が含まれる
- **WHEN** `compute_coherence_score(project_dir)` を呼び出す
- **THEN** 戻り値の dict に `overall`, `coverage`, `consistency`, `completeness`, `efficiency`, `details` キーが含まれる

#### Scenario: プロジェクトに .claude/ ディレクトリが存在しない
- **WHEN** `project_dir` に `.claude/` ディレクトリが存在しない
- **THEN** coverage スコアは 0.0 を返し、他の軸も存在するレイヤーのみでスコアを算出する
