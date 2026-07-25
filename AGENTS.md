# Codex adapter — evolve-anything

作業開始前に [共通Agent Policy](docs/agent-contract/policy.md) を全文読むこと。
製品仕様のSoTは `SPEC.md`、`spec/`、`docs/decisions/`、Claude Code Plugin固有の実態は
`CLAUDE.md`、`.claude-plugin/`、`hooks/hooks.json` にある。

## Codex固有

- 通常のprimary executorはClaude Code。Codexはユーザー指定、cold review、独立検証で使う。
- top-level実装はrepo外の`codex/<issue>-<slug>` laneで行う。
- `.claude-plugin`、`claude plugin validate`などClaude側の実在識別子をCodex名へ置換しない。
- Codex CLI/plugin/hooksの存在はhelp・実設定で確認し、Claude形式との互換を推測しない。
- `.codex/agents/*.toml`はCodex schemaで独立定義する。

機能差は [Capability Matrix](docs/agent-contract/capability-matrix.md) を参照する。
