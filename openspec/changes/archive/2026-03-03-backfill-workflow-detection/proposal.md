## Why

backfill のワークフロー検出は現在 `Skill→Agent` パターンのみを捕捉するが、実データでは `TeamCreate→Agent` や連続 Agent パターンが大半を占める。5プロジェクト（915 sessions）の調査で Agent 呼び出し 181 件中、ワークフローとして記録されたのは 10 件のみ（5.5%）。atlas-breeaders では TeamCreate 7セッション・Agent 49回あるのにワークフローは1件しか拾えていない。Phase C（ワークフロー構造進化）の設計入力に十分なデータが集まらない。

## What Changes

- `backfill.py`: ワークフロー検出ロジックを拡張し、以下のパターンを新たに捕捉する
  - `TeamCreate→Agent` パターン: チーム結成から終了までの Agent 起動をワークフローとして記録
  - 連続 Agent パターン: Skill なしでも短時間内の連続 Agent 起動をワークフローとみなす
- `parse_transcript()`: TeamCreate/SendMessage/TeamDelete のツール呼び出しをワークフロー境界として認識
- `workflows.jsonl`: ワークフロータイプ（`skill-driven` / `team-driven` / `agent-burst`）を記録するフィールド追加

## Capabilities

### New Capabilities
- `team-workflow-detection`: TeamCreate→Agent パターンをワークフローとして検出・記録する機能
- `agent-burst-detection`: Skill/Team なしの連続 Agent 起動をワークフローとしてグルーピングする機能

### Modified Capabilities
- `backfill`: ワークフロー検出ロジックの拡張。`workflows.jsonl` に `workflow_type` フィールド追加

## Impact

- `skills/backfill/scripts/backfill.py` — ワークフロー検出ロジック拡張
- `skills/backfill/scripts/tests/test_backfill.py` — 新パターンのテスト追加
- `~/.claude/rl-anything/workflows.jsonl` — 既存レコードとの互換性（`workflow_type` 未設定は `skill-driven` として扱う）
- Phase C（workflow-tracing 11.1〜11.4）のデータ入力が大幅に増加する見込み
