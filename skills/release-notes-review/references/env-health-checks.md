# グローバル環境健康診断の詳細チェック項目

[SKILL.md](../SKILL.md) の Step 2.5 から参照される、フルモード時のみ実施する詳細診断。
軽量モード時はここを読まず SKILL.md Step 1.5 の構造チェック（件数 + pin + hook 実在 + MEMORY 行数）で代替する。

## 2.5.1 Global Rules (`~/.claude/rules/*.md`)

以下を検査:

- **行数チェック**: `rules-style.md` ルールに従い、frontmatter 除外で 3 行以内か
- **重複検出**: 複数ルールが同じことを異なる表現で指示していないか
- **矛盾検出**: ルール間で相反する指示がないか
- **陳腐化チェック**: 参照先ツール/ワークフロー/スキルが現在も存在するか
- **CC 代替チェック**: CC 新機能がルールの役割を吸収していないか
  （例: CC がビルトインで提供するようになった機能を手動ルールで指示している場合）

## 2.5.2 Global Skills (`~/.claude/skills/*/SKILL.md`)

gstack 内蔵スキル（`~/.claude/skills/gstack/` 配下および `~/.claude/skills/gstack-*/`）を除外し、自作/サードパーティを対象:

- **CC 機能重複チェック**: CC 新機能が自作スキルの役割を吸収していないか
- **frontmatter 品質**: name, description が存在するか。description にトリガーワードがあるか
- **新機能活用チャンス**: CC の新機能で既存スキルを強化できないか
  （例: 新しい frontmatter フィールド、skill hooks、context:fork 等）

## 2.5.3 Global Agents (`~/.claude/agents/*.md`)

各エージェント定義を Read で確認:

- **品質チェック**: model 指定、maxTurns、disallowedTools の有無
- **新機能活用**: CC の新エージェント機能（memory スコープ、isolation:worktree 等）の活用余地
- **参照の有効性**: 参照しているスキルやツールが現存するか

## 2.5.4 Settings Hooks (`~/.claude/settings.json`)

hooks 定義を確認:

- **孤立検出**: 参照先スクリプトが存在するか
- **新フックイベント活用**: CC が新たに追加したフックイベント（PostCompact, WorktreeCreate 等）の活用余地
- **evolve-anything hooks との整合**: プラグインの hooks.json と settings.json で重複・競合がないか

**重複判定は `if` 条件まで見る（誤検出防止・MUST）**: CC の hook の実効的な同一性は `command` 単独でなく
`(event, matcher, command, if)` の組で決まる。同じ `command` でも `matcher` や `if` 条件
（例: `if: Skill(gstack-ship)` と `if: Skill(commit)`）が異なれば、それは**別トリガーであり重複ではない**。
hook を列挙・比較するときは command だけを抜き出さず、必ず `matcher` と `if` を併記して突合する。
`if` を落として command だけで数えると、発火条件の違う hook を重複と誤検出する。
Bash/Python で settings.json をパースして確認する場合も、`if` フィールドを必ず出力に含めること。

## 2.5.5 Memory (`~/.claude/projects/*/memory/MEMORY.md`)

現在プロジェクトの MEMORY.md を Read で確認:

- **エントリ数**: 200 行の上限に対する使用率
- **陳腐化チェック**: 古いバージョン番号、完了済みタスク、存在しないファイルへの参照
