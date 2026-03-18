## Why

スキルに自己進化パターン（Pre-flight / Failure-triggered Learning / pitfalls.md）を組み込むには、現状 `evolve` パイプライン全体を回す必要がある（skill_evolve_assessment → remediation）。特定スキルをピンポイントで自己進化対応にしたいユースケースに対してオーバーヘッドが大きい。自己進化パターン組み込みは1回限りの操作であり、独立コマンドとして提供するのが自然。加えて rl-loop の便利フラグとしても統合し、最適化と同時に自己進化対応できるようにする。

## What Changes

- **独立コマンド `/rl-anything:evolve-skill <name>`** を新設（主）。適性判定→テンプレート組み込み→人間確認を1コマンドで実行
- `run-loop.py` に `--evolve` フラグを追加（副）。未対応スキルの場合にループ内で自己進化パターン組み込みを提案
- `scripts/lib/skill_evolve.py` の既存 API（`compute_telemetry_scores`, `compute_llm_scores`, `detect_anti_patterns`, `evolve_skill_proposal`）を再利用

## Capabilities

### New Capabilities
- `evolve-skill-command`: 独立コマンド `/rl-anything:evolve-skill` によるスキル自己進化パターン組み込み
- `rl-loop-evolve-step`: rl-loop のループ内 `--evolve` フラグによる自己進化パターン組み込み

### Modified Capabilities

## Impact

- `skills/evolve-skill/` — 新スキルディレクトリ（SKILL.md）
- `skills/rl-loop-orchestrator/scripts/run-loop.py` — `--evolve` フラグ + Step 5.5 追加
- `skills/rl-loop-orchestrator/SKILL.md` — `--evolve` オプション説明追加
- `scripts/lib/skill_evolve.py` — 既存 API を再利用（変更なし）
- `skills/evolve/templates/` — 既存テンプレートを再利用（変更なし）
