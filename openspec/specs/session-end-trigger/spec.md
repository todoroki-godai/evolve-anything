# session-end-trigger Specification

## Purpose
セッション終了時の evolve トリガー。session_summary hook (Stop) の拡張として trigger_engine を呼び出し、条件を満たした場合に pending-trigger.json 経由で次回 SessionStart 時にユーザーへ提案メッセージを配信する。audit overdue 検出およびスキル変更検出を含む。

## Requirements
### Requirement: Evolve suggestion on session end
`session_summary.py` (Stop hook) のセッション終了処理に、`trigger_engine` を呼び出して evolve 実行条件を評価するステップを追加しなければならない (SHALL)。

#### Scenario: Trigger condition met
- **WHEN** セッション終了時に `trigger_engine.evaluate()` が `triggered=True` を返す
- **THEN** `pending-trigger.json` にトリガー結果（reason、推奨コマンド、メッセージ）を書き出さなければならない (MUST)

#### Scenario: No trigger condition met
- **WHEN** セッション終了時に `trigger_engine.evaluate()` が `triggered=False` を返す
- **THEN** `pending-trigger.json` を書き出してはならない (MUST NOT)（サイレント）

#### Scenario: Trigger engine error
- **WHEN** `trigger_engine.evaluate()` が例外を発生させた
- **THEN** 例外をキャッチし、セッション終了処理を続行しなければならない (MUST)（サイレント失敗）

### Requirement: Pending trigger delivery on SessionStart
`restore_state.py` (SessionStart hook) が `pending-trigger.json` を読み取り、トリガー提案メッセージを stdout に出力しなければならない (SHALL)。

#### Scenario: Pending trigger file exists
- **WHEN** `pending-trigger.json` が存在する
- **THEN** ファイル内容を読み取り、提案メッセージを stdout に出力し、ファイルを削除しなければならない (MUST)

#### Scenario: Pending trigger file does not exist
- **WHEN** `pending-trigger.json` が存在しない
- **THEN** 何もせずに処理を続行しなければならない (SHALL)

#### Scenario: Pending trigger file read error
- **WHEN** `pending-trigger.json` の読み取りまたはパースに失敗した
- **THEN** エラーを stderr に出力し、ファイルを削除して処理を続行しなければならない (MUST)

### Requirement: Per-session deduplication
1 セッション内でトリガーメッセージは最大1回としなければならない (SHALL)。

#### Scenario: Multiple triggers in same session
- **WHEN** session_end と corrections の両方がトリガー条件を満たす
- **THEN** session_end 側のみメッセージを出力しなければならない (SHALL)（Stop hook は1回のみ実行されるため自然に保証）

### Requirement: Skill change detection
セッション中に `.claude/skills/*/SKILL.md` が変更された場合、追加のコンテキストを提案メッセージに含めなければならない (SHALL)。

#### Scenario: Skill files modified in session
- **WHEN** セッション中に `git diff` で `.claude/skills/*/SKILL.md` に変更が検出される
- **THEN** 提案メッセージに「変更されたスキル: {skill_names}」を追加し、`/rl-anything:optimize {skill}` を推奨しなければならない (MUST)

#### Scenario: No skill files modified
- **WHEN** セッション中に SKILL.md の変更が検出されない
- **THEN** 通常の evolve 提案メッセージのみ出力しなければならない (SHALL)

### Requirement: Audit overdue detection
前回 audit からの経過日数を評価し、一定期間を超えた場合に audit 実行を提案しなければならない (SHALL)。

#### Scenario: Audit overdue
- **WHEN** `evolve-state.json` の `last_audit_timestamp` から `interval_days`（デフォルト: 30）日以上経過
- **THEN** `pending-trigger.json` に audit 提案（`/rl-anything:audit`）を含めなければならない (MUST)

#### Scenario: Audit not overdue
- **WHEN** 前回 audit からの経過が `interval_days` 未満
- **THEN** audit 提案を含めてはならない (MUST NOT)

#### Scenario: No previous audit record
- **WHEN** `last_audit_timestamp` が存在しない
- **THEN** audit 未実行とみなし、audit 実行を提案しなければならない (SHALL)
