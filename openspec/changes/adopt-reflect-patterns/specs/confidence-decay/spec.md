## ADDED Requirements

### Requirement: prune 集計時に decay スコアを計算しなければならない（MUST）
prune.py の淘汰判定時に、各スキルの使用レコードに対して `confidence = base_score * exp(-age_days / decay_days)` を計算しなければならない（MUST）。decay_days のデフォルトは 90 日とする。

#### Scenario: 最近使用されたスキル
- **WHEN** スキル A が 5 日前に使用され、corrections なし
- **THEN** confidence は `1.0 * exp(-5/90)` ≈ 0.946 となり、淘汰候補にならない

#### Scenario: 長期未使用スキル
- **WHEN** スキル B が 180 日前に最後に使用され、corrections なし
- **THEN** confidence は `1.0 * exp(-180/90)` ≈ 0.135 となり、淘汰候補になる

#### Scenario: corrections による減点
- **WHEN** スキル C が 10 日前に使用され、2 件の correction が紐付いている
- **THEN** base_score が `1.0 - (0.15 * 2)` = 0.7 に減点された上で decay が適用される

#### Scenario: corrections.jsonl が存在しない場合
- **WHEN** corrections.jsonl ファイルが存在しない
- **THEN** 全スキルの base_score を 1.0 として扱い、corrections 減点を行わない（decay 計算のみ適用）

### Requirement: pin マークによる淘汰保護
スキルディレクトリに `.pin` ファイルが存在する場合、decay スコアに関わらず淘汰候補から除外する（MUST）。`.pin` は空ファイルとし、`touch <skill-dir>/.pin` で作成する。ファイルの中身は参照しない。

#### Scenario: pin されたスキルは淘汰されない
- **WHEN** `~/.claude/skills/ooishi-design-system/.pin` が存在し（空ファイル）、180 日間未使用
- **THEN** prune の淘汰候補リストに含まれない

#### Scenario: pin ファイルがない場合は通常判定
- **WHEN** `~/.claude/skills/some-skill/` に `.pin` ファイルがなく、decay スコアが閾値以下
- **THEN** prune の淘汰候補リストに含まれる

### Requirement: decay 閾値の設定が可能でなければならない（MUST）
淘汰候補とする confidence 閾値のデフォルトは 0.2 としなければならない（MUST）。evolve-state.json で `decay_threshold` として上書き可能とする。

#### Scenario: デフォルト閾値
- **WHEN** evolve-state.json に `decay_threshold` が未設定
- **THEN** confidence < 0.2 のスキルが淘汰候補となる
