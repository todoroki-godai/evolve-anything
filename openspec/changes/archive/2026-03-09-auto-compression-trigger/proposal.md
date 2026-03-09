Related: #21

## Why

bloat check レポートは audit スキルで実装済みだが、肥大化の検出→圧縮アクションの提案は手動 `/evolve` または `/audit` 実行時のみ。セッション間で bloat が進行しても気づけず、MEMORY.md 200行ハードリミット到達やルール遵守率低下が起きてから対処する後追い状態。auto-evolve-trigger と同パターンで「bloat 検出時に自動で圧縮アクションを提案」すれば、肥大化を未然に防止できる。

## What Changes

- `trigger_engine.py` に `evaluate_bloat()` トリガーを追加。既存の `bloat_check()` (scripts/bloat_control.py) を呼び出し、閾値超過時に圧縮アクションを提案
- session_end 評価フローに bloat トリガーを統合（既存の session_count/days_elapsed/audit_overdue と同列）
- bloat 種別ごとに適切なアクションを提案:
  - MEMORY.md 超過 → `/rl-anything:evolve`（分割提案）
  - rules 総数超過 → `/rl-anything:evolve`（統合・prune 提案）
  - skills 総数超過 → `/rl-anything:evolve`（archive・prune 提案）
  - CLAUDE.md 超過 → `/rl-anything:evolve`（分割提案）
- `DEFAULT_TRIGGER_CONFIG` に `bloat` トリガー設定を追加（`enabled` のみ。閾値は `bloat_control.BLOAT_THRESHOLDS` が single source of truth）

## Capabilities

### New Capabilities
- `bloat-trigger`: bloat_check() 結果に基づくトリガー評価。セッション終了時に自動で肥大化を検出し圧縮アクションを提案する

### Modified Capabilities
- `session-end-trigger`: bloat トリガーを session_end 評価フローに統合

## Impact

- `scripts/lib/trigger_engine.py` — `evaluate_bloat()` 追加、`evaluate_session_end()` に bloat 条件統合
- `scripts/bloat_control.py` — `bloat_check()` を trigger_engine から呼び出し（変更なし、依存追加のみ）
- `hooks/session_summary.py` — bloat トリガー結果を pending-trigger に含める（既存フロー内）
- テスト追加: `scripts/lib/tests/test_trigger_engine.py`
