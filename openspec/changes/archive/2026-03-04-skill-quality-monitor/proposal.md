## Why

`/optimize` 実行時にベースラインスコアが generations/ に保存され、observe hooks で usage.jsonl に使用回数が記録されているが、高頻度で使われる global スキル（commit, openspec-refine 等）の品質が時間とともに劣化しているかを検知する仕組みがない。劣化に気づかないまま使い続けると、ワークフロー全体の品質低下につながる。

## What Changes

- **品質ベースライン記録**: global スキルの品質スコアを定期的に計測し、履歴として保存する仕組みを追加
- **品質推移の可視化**: `/audit` レポートに品質推移セクションを追加し、スコア変動を視覚的に表示
- **劣化検知エンジン**: 一定使用回数または一定期間経過で再スコアリングを実行し、スコア低下を自動検知
- **optimize 推奨通知**: 劣化検知時に `/optimize` の実行を推奨する通知をレポートに含める

## Capabilities

### New Capabilities

- `quality-baseline`: 高頻度 global スキルの品質スコアを計測・記録し、ベースライン履歴として ~/.claude/rl-anything/quality-baselines.jsonl に保存する機能
- `degradation-detector`: 使用回数閾値（50回）または期間閾値（7日）で再スコアリングをトリガーし、ベースラインからのスコア低下（10%以上）を検知する機能

### Modified Capabilities

- `audit-report`: 品質推移セクションの追加。高頻度 global スキルのスコア履歴・劣化警告・optimize 推奨を1画面レポートに統合

## Impact

- **新規ファイル**: scripts/quality_monitor.py（品質計測・ベースライン記録・劣化検知ロジック）
- **既存変更**: skills/audit/scripts/audit.py（品質推移セクションの追加）、skills/audit/SKILL.md（品質モニタリング手順の追加）
- **データストレージ**: ~/.claude/rl-anything/quality-baselines.jsonl（品質スコア履歴）
- **依存**: 既存の optimize.py の _llm_evaluate() を再利用（品質スコア計測）、usage.jsonl（使用回数の集計）
- **外部依存**: なし（既存の claude CLI のみ使用）
