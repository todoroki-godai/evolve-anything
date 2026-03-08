# corrections-threshold-trigger Specification

## Purpose
corrections 蓄積時の再最適化トリガー。correction_detect hook (PostToolUse) の拡張として corrections 件数を監視し、閾値到達時に関連スキルの再最適化（optimize/reflect）を提案する。

## Requirements
### Requirement: Corrections accumulation trigger
`correction_detect.py` (PostToolUse hook) で corrections の蓄積件数を監視し、閾値到達時に関連スキルの再最適化を提案しなければならない (SHALL)。

#### Scenario: Threshold reached
- **WHEN** 前回 evolve/reflect 以降の corrections 件数が `threshold`（デフォルト: 10）に到達
- **THEN** stdout にメッセージを出力し、関連スキルの `/rl-anything:optimize <skill>` または `/rl-anything:reflect` を推奨しなければならない (MUST)

#### Scenario: Below threshold
- **WHEN** corrections 件数が閾値未満
- **THEN** 何も出力してはならない (MUST NOT)

#### Scenario: Cooldown active
- **WHEN** corrections 閾値に到達したが、同一 reason のトリガーがクールダウン期間内に発火済み
- **THEN** 何も出力してはならない (MUST NOT)

### Requirement: Related skill identification
correction レコードから再最適化対象のスキルを特定しなければならない (SHALL)。

#### Scenario: Correction has last_skill
- **WHEN** correction レコードに `last_skill` フィールドが存在する
- **THEN** 当該スキルに対する `/rl-anything:optimize <skill>` を推奨しなければならない (MUST)

#### Scenario: Correction without last_skill
- **WHEN** correction レコードに `last_skill` が空または未設定
- **THEN** 汎用の `/rl-anything:evolve` を推奨しなければならない (SHALL)

#### Scenario: Multiple skills with corrections
- **WHEN** 複数のスキルに対する corrections が蓄積されている
- **THEN** corrections 件数が最も多いスキル上位3件を推奨対象としなければならない (MUST)
