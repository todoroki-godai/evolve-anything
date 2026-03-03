## ADDED Requirements

### Requirement: classify_artifact_origin utility
`scripts/audit.py` に `classify_artifact_origin(path: Path) -> str` 関数を追加しなければならない (MUST)。
戻り値は `"custom"` / `"plugin"` / `"global"` のいずれかとする (SHALL)。

判定ロジック:
- `~/.claude/plugins/cache/` 配下 → `"plugin"`
- `~/.claude/skills/` 配下 → `"global"`
- その他 → `"custom"`

引数の `path` はチルダ展開済みの絶対パスでなければならない (MUST)。
関数内で `Path.expanduser()` を呼び出してチルダを展開しなければならない (MUST)。

環境変数 `CLAUDE_PLUGINS_DIR` が設定されている場合はそちらを優先しなければならない (MUST)。

#### Scenario: プラグインキャッシュ配下のスキル
- **WHEN** `classify_artifact_origin(Path.home() / ".claude" / "plugins" / "cache" / "rl-anything" / "rl-anything" / "0.4.0" / ".claude" / "skills" / "optimize" / "SKILL.md")` を呼び出す
- **THEN** `"plugin"` を返す

#### Scenario: チルダ付きパスの展開
- **WHEN** `classify_artifact_origin(Path("~/.claude/plugins/cache/rl-anything/rl-anything/0.4.0/.claude/skills/optimize/SKILL.md"))` を呼び出す
- **THEN** 関数内でチルダが展開され、`"plugin"` を返す

#### Scenario: グローバルスキル
- **WHEN** `classify_artifact_origin(Path.home() / ".claude" / "skills" / "my-skill" / "SKILL.md")` を呼び出す
- **THEN** `"global"` を返す

#### Scenario: プロジェクトローカルのカスタムスキル
- **WHEN** `classify_artifact_origin(Path("/Users/user/project/.claude/skills/my-skill/SKILL.md"))` を呼び出す
- **THEN** `"custom"` を返す

#### Scenario: 環境変数によるプラグインパスオーバーライド
- **WHEN** `CLAUDE_PLUGINS_DIR=/custom/plugins` が設定されている
- **THEN** `/custom/plugins/` 配下のパスは `"plugin"` と判定されなければならない (MUST)

### Requirement: prune がプラグインスキルを淘汰対象から除外する
prune はプラグイン由来スキルを淘汰候補から除外しなければならない (MUST)。
`detect_zero_invocations()` はプラグイン由来のスキル（origin == "plugin"）を
淘汰候補リスト (`zero_invocations`) に含めず、
代わりに `plugin_unused` カテゴリとしてレポートのみ出力しなければならない (SHALL)。

#### Scenario: プラグインスキルのゼロ呼び出し
- **WHEN** プラグイン由来スキルが30日間呼び出されていない
- **THEN** `zero_invocations` リストには含まれず、`plugin_unused` リストに含まれなければならない (MUST)

#### Scenario: カスタムスキルのゼロ呼び出し
- **WHEN** プロジェクトローカルのカスタムスキルが30日間呼び出されていない
- **THEN** `zero_invocations` リストに含まれなければならない (SHALL)（従来通り）

### Requirement: evolve レポートの出自別セクション
evolve の SKILL.md が生成するレポートは、prune 結果をスキルの出自別に表示しなければならない (MUST)。
セクション: Custom（淘汰候補）、Plugin（レポートのみ）、Global（既存ロジック維持）。

#### Scenario: 混在環境でのレポート
- **WHEN** カスタムスキル、プラグインスキル、グローバルスキルが混在するプロジェクトで evolve を実行
- **THEN** レポートに3つのセクションが表示され、各スキルが正しいセクションに分類されなければならない (MUST)

### Requirement: run_prune の戻り値に plugin_unused を追加
`run_prune()` の戻り値辞書に `plugin_unused` キーを追加しなければならない (MUST)。
型は `List[Dict[str, Any]]` とし (SHALL)、各要素は `file`, `skill_name`, `reason: "plugin_unused"` を含まなければならない (MUST)。

#### Scenario: run_prune の戻り値構造
- **WHEN** `run_prune()` を実行する
- **THEN** 戻り値に `plugin_unused` キーが存在し、プラグイン由来の未使用スキルが格納されなければならない (MUST)

### Requirement: ルールは常に custom として扱う
`classify_artifact_origin` はルール（rules）に対しても呼び出される可能性があり、ルールは常に `"custom"` として分類しなければならない (MUST)。
プラグイン由来のルールは現時点では存在しないため、この分類で十分である。
将来プラグイン由来ルールが追加された場合は、スキルと同様のパスベース判定で対応する (SHALL)。

#### Scenario: ルールの出自分類
- **WHEN** `classify_artifact_origin(Path("/Users/user/project/.claude/rules/my-rule.md"))` を呼び出す
- **THEN** `"custom"` を返さなければならない (MUST)
