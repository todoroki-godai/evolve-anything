# bloat-trigger Specification

## Purpose
`trigger_engine` モジュールで `bloat_check()` の結果に基づいて肥大化トリガーを評価する。閾値超過が検出された場合、圧縮アクションを提案する `TriggerResult` を返す。

## Requirements
### Requirement: Bloat trigger evaluation
`trigger_engine` モジュールは `bloat_check()` の結果に基づいて肥大化トリガーを評価しなければならない (SHALL)。閾値超過が検出された場合、圧縮アクションを提案する `TriggerResult` を返さなければならない (MUST)。

#### Scenario: MEMORY.md exceeds threshold
- **WHEN** プロジェクトの MEMORY.md が `memory_md_lines`（デフォルト: 150）行を超過
- **THEN** `TriggerResult(triggered=True, reason="bloat", action="/rl-anything:evolve")` を返し、details に bloat 種別 `memory` と行数を含めなければならない (MUST)

#### Scenario: Rules count exceeds threshold
- **WHEN** rules の総数が `rules_count`（デフォルト: 100）を超過
- **THEN** `TriggerResult(triggered=True, reason="bloat", action="/rl-anything:evolve")` を返し、details に bloat 種別 `rules_count` と件数を含めなければならない (MUST)

#### Scenario: Skills count exceeds threshold
- **WHEN** skills の総数が `skills_count`（デフォルト: 30）を超過
- **THEN** `TriggerResult(triggered=True, reason="bloat", action="/rl-anything:evolve")` を返し、details に bloat 種別 `skills_count` と件数を含めなければならない (MUST)

#### Scenario: CLAUDE.md exceeds threshold
- **WHEN** CLAUDE.md が `claude_md_lines`（デフォルト: 150）行を超過
- **THEN** `TriggerResult(triggered=True, reason="bloat", action="/rl-anything:evolve")` を返し、details に bloat 種別 `claude_md` と行数を含めなければならない (MUST)

#### Scenario: No bloat detected
- **WHEN** すべてのアーティファクトが閾値以内
- **THEN** bloat 条件では `triggered=False` としなければならない (MUST)

#### Scenario: Bloat check error
- **WHEN** `bloat_check()` の呼び出しで例外が発生
- **THEN** 例外をキャッチし、bloat トリガーをスキップして他のトリガー評価を続行しなければならない (MUST)

#### Scenario: bloat_check() import failure
- **WHEN** `bloat_control` モジュールの import に失敗（ImportError）
- **THEN** bloat トリガー評価をスキップし、他のトリガー評価を続行しなければならない (MUST)

### Requirement: Bloat trigger configuration
`trigger_config.triggers.bloat` キーで bloat トリガーの有効/無効を制御できなければならない (SHALL)。閾値は `bloat_control.BLOAT_THRESHOLDS` が single source of truth であり、trigger_config では管理しない。

#### Scenario: Bloat trigger disabled
- **WHEN** `trigger_config.triggers.bloat.enabled` が `false`
- **THEN** bloat トリガー評価をスキップしなければならない (MUST)

#### Scenario: No bloat config specified
- **WHEN** `trigger_config` に `bloat` キーが存在しない
- **THEN** bloat トリガーはデフォルトで有効とし、`bloat_control.BLOAT_THRESHOLDS` の閾値で動作しなければならない (SHALL)

### Requirement: Bloat trigger cooldown
bloat トリガーは既存のクールダウン機構を利用し、reason `"bloat"` で連続発火を防止しなければならない (SHALL)。

#### Scenario: Within cooldown
- **WHEN** 前回の bloat トリガー発火から `cooldown_hours` 未満
- **THEN** bloat 条件でのトリガーをスキップしなければならない (MUST)

#### Scenario: Cooldown expired
- **WHEN** 前回の bloat トリガー発火から `cooldown_hours` 以上経過
- **THEN** bloat 条件を通常通り評価しなければならない (SHALL)

### Requirement: Bloat details in trigger message
bloat トリガーのメッセージは、検出された bloat 種別と具体的な数値を含めなければならない (SHALL)。

#### Scenario: Single bloat type detected
- **WHEN** MEMORY.md のみが閾値超過（180/150行）
- **THEN** メッセージに「MEMORY.md が 180/150 行で超過」を含めなければならない (MUST)

#### Scenario: Multiple bloat types detected
- **WHEN** MEMORY.md と rules 総数の両方が閾値超過
- **THEN** メッセージに両方の超過情報を含めなければならない (MUST)

#### Scenario: All bloat types detected simultaneously
- **WHEN** MEMORY.md、rules 総数、skills 総数、CLAUDE.md の全てが閾値超過
- **THEN** メッセージに全4種別の超過情報を含め、単一の `TriggerResult(reason="bloat")` で返さなければならない (MUST)
