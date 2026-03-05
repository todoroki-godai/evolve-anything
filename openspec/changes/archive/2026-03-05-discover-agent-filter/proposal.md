## Why

discover フェーズが Claude Code 組み込みの Agent タイプ（`Agent:Explore`, `Agent:Plan`, `Agent:general-purpose`）をユーザー定義スキルと同列にメインランキングに表示している。これらは最適化・進化の対象外であり、スキル候補として提案するのは不適切。一方、カスタム Agent（`.claude/agents/` 定義）はユーザーが最適化可能なためメインランキングに残す必要がある。

## What Changes

- 組み込み Agent（`Explore`, `Plan`, `general-purpose` 等）をメインランキングから除外し、`agent_usage_summary` として別セクションに分離表示（info_only）
- カスタム Agent の判定ロジックを追加: 既知の組み込み名リスト + `~/.claude/agents/` と `.claude/agents/` の走査を併用
- カスタム Agent はメインランキングに残し、global/project スコープを正しく判定
- プラグイン Agent の扱いは現状維持（plugin_summary）

## Capabilities

### New Capabilities
- `agent-type-classification`: Agent:XX パターンを組み込み/カスタム(global)/カスタム(project)/プラグインに分類するロジック

### Modified Capabilities
- `scope-detection`: カスタム Agent のスコープ判定（global: `~/.claude/agents/`、project: `.claude/agents/`）を追加

## Impact

- `skills/discover/scripts/discover.py`: `detect_behavior_patterns()` にフィルタリングロジック追加、`agent_usage_summary` 出力追加
- `skills/discover/scripts/tests/`: 新規テスト追加
- SKILL.md の出力フォーマット説明を更新（agent_usage_summary セクション追加）
