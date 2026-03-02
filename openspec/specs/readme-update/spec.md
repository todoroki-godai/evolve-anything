## ADDED Requirements

### Requirement: README.md のストーリー構成

README.md を Before/After のストーリー構成で全面更新する。導入前の課題、Plugin の概要、導入後の効果を描く。

#### Scenario: Before（課題）セクションが存在する
- **WHEN** README.md を確認する
- **THEN** 「スキル/ルールの手動改善に時間がかかる」「改善の品質が属人的」等の導入前の課題が記述されている

#### Scenario: What（概要）セクションが存在する
- **WHEN** README.md を確認する
- **THEN** rl-anything が何をするか（遺伝的アルゴリズムによるスキル/ルールの自動最適化）が簡潔に記述されている

#### Scenario: After（効果）セクションが存在する
- **WHEN** README.md を確認する
- **THEN** 導入後の具体的な効果（スラッシュコマンドで即座に最適化開始、定量的なスコアによる品質管理）が記述されている

### Requirement: README.md のクイックスタートがスラッシュコマンド形式

README.md のクイックスタートは `python3 <PLUGIN_DIR>/...` ではなく、スラッシュコマンド形式で記述する。

#### Scenario: /optimize のクイックスタートが記載されている
- **WHEN** README.md のクイックスタートセクションを確認する
- **THEN** `/optimize --target .claude/skills/my-skill/SKILL.md` 形式の使用例が記載されている

#### Scenario: /rl-loop のクイックスタートが記載されている
- **WHEN** README.md のクイックスタートセクションを確認する
- **THEN** `/rl-loop --target .claude/skills/my-skill/SKILL.md` 形式の使用例が記載されている

#### Scenario: python3 コマンドの直接実行は詳細リファレンスに移動
- **WHEN** README.md のクイックスタートセクションを確認する
- **THEN** `python3 <PLUGIN_DIR>/...` 形式のコマンドはクイックスタートに含まれず、詳細リファレンスセクションに記載されている

### Requirement: CLAUDE.md のクイックスタート同期

CLAUDE.md のクイックスタートセクションを README.md と一致するスラッシュコマンド形式に更新する。

#### Scenario: CLAUDE.md のクイックスタートがスラッシュコマンド形式
- **WHEN** CLAUDE.md のクイックスタートセクションを確認する
- **THEN** `/optimize`, `/rl-loop` 形式の使用例が記載されている

#### Scenario: CLAUDE.md の他のセクションは維持
- **WHEN** CLAUDE.md のクイックスタート以外のセクション（コンポーネント表、適応度関数、テスト）を確認する
- **THEN** 既存の内容が維持されている（ただし、スラッシュコマンド対応に伴う軽微な文言修正は許容）

### Requirement: README.md と CLAUDE.md の整合性

README.md と CLAUDE.md のクイックスタートセクションに矛盾がないことを保証する。

#### Scenario: コマンド形式の一致
- **WHEN** README.md と CLAUDE.md のクイックスタートを比較する
- **THEN** 同一のスラッシュコマンド形式（`/optimize`, `/rl-loop`）が使用されている

#### Scenario: 引数の一致
- **WHEN** README.md と CLAUDE.md で記載されている引数オプションを比較する
- **THEN** 同一の引数セットが記載されている（表現の差異は許容するが、引数名・デフォルト値は一致）
