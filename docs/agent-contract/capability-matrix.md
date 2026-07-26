# Claude Code / Codex Capability Matrix

2026-07-26時点の実測・repo契約。version差があり得るため、コマンドを変更するときは
各CLIのhelpと実設定を再確認する。

| 意図 | Claude Code | Codex | 契約 |
|---|---|---|---|
| project instruction | `CLAUDE.md` | `AGENTS.md` | 両入口から`policy.md`を必読化 |
| project agent | `.claude/agents/*.md` | `.codex/agents/*.toml` | schemaを機械置換しない |
| plugin管理 | `claude plugin ...` | `codex plugin add/list/marketplace/remove` | subcommand対応をhelpで確認 |
| plugin validate | `claude plugin validate` | 同名subcommandなし | Claude側で実行 |
| hooks | plugin `hooks/hooks.json` | `~/.codex/hooks.json`等 | payload互換をfixtureで確認してから共有 |
| approval rules | Claude permissions | `~/.codex/rules/default.rules` | instruction層と混同しない |
| usage観測 | Claude stores/hooks | `state_5.sqlite`＋hooks | recordにruntimeを保持 |
| top-level worktree | repo外lane | repo外lane | `evolve-agent-task start` |

## 禁止する機械置換指紋

- `.Codex-plugin`
- `<repo>/.Codex/`（`<repo>/.claude/`へ自動修復）
- `~/.Codex/`（`~/.codex/`へ自動修復）
- 文脈不明な裸の`.Codex/`（auditのみ・自動置換しない）
- `Codex plugin validate`
- `Codex Code`（復元先が一意でないためauditのみ・自動置換しない）

既知指紋の棚卸しは `bin/evolve-codex-config-cleanup audit`、修復はhash付きplanと明示承認を
経由する。未知の文字列は自動修正しない。
