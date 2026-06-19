## ADDED Requirements

### Requirement: Scope auto-detection from file path

optimize.py はターゲットスキルのファイルパスから scope を自動判定する。`~/.claude/skills/` 配下のファイルは `global`、それ以外は `project` と判定する。

追加: discover の `determine_scope()` は Agent:XX パターンに対して MUST `classify_agent_type()` を使用し、カスタム Agent の scope をディレクトリ由来で判定する。`~/.claude/agents/` 由来は `global`、`.claude/agents/` 由来は `project`。組み込み Agent（`classify_agent_type()` が `"builtin"` を返す）に対しては MUST scope 判定をスキップする（`agent_usage_summary` に分離されるため）。

#### Scenario: Global skill detection

- **WHEN** ターゲットパスが `~/.claude/skills/commit/SKILL.md` である
- **THEN** scope は MUST `global` と判定される

#### Scenario: Project skill detection

- **WHEN** ターゲットパスが `/path/to/project/.claude/skills/my-skill/SKILL.md` である
- **THEN** scope は MUST `project` と判定される

#### Scenario: Plugin skill detection

- **WHEN** ターゲットパスがプラグインディレクトリ（`~/.claude/evolve-anything/skills/` 等）配下である
- **THEN** scope は MUST `global` と判定される

#### Scenario: Custom global Agent scope detection

- **WHEN** `Agent:my-agent` が `classify_agent_type()` で `"custom_global"` と判定される
- **THEN** discover の `determine_scope()` は MUST `global` を返す

#### Scenario: Custom project Agent scope detection

- **WHEN** `Agent:my-agent` が `classify_agent_type()` で `"custom_project"` と判定される
- **THEN** discover の `determine_scope()` は MUST `project` を返す

#### Scenario: Built-in Agent scope is not determined

- **WHEN** `Agent:Explore` が `classify_agent_type()` で `"builtin"` と判定される
- **THEN** `determine_scope()` は当該パターンに対して呼ばれない（上流で `agent_usage_summary` に分離済み）

### Requirement: Scope display in startup message

optimize.py は実行開始時にターゲットの scope を表示する。

#### Scenario: Global skill notification

- **WHEN** scope が `global` のターゲットで最適化を開始する
- **THEN** 「汎用評価モードで最適化します（プロジェクト固有のコンテキストは使用しません）」と表示される

#### Scenario: Project skill notification

- **WHEN** scope が `project` のターゲットで最適化を開始する
- **THEN** scope に関する特別な通知は表示されない（既存動作）

### Requirement: Scope label in SKILL.md target selection

SKILL.md のターゲット選択指示において、候補スキルに scope ラベルを表示する。

#### Scenario: Target list with scope labels

- **WHEN** LLM がターゲット候補をユーザーに提示する
- **THEN** 各候補に `[global]` または `[project]` ラベルが併記される
