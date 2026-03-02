## ADDED Requirements

### Requirement: SKILL.md のスキル名変更

genetic-prompt-optimizer の SKILL.md frontmatter `name` フィールドを `optimize` に変更する。rl-loop-orchestrator の SKILL.md は `rl-loop` をトリガーワードとして維持する。

#### Scenario: /optimize でスキルが呼び出される
- **WHEN** ユーザーが `/optimize` と入力する
- **THEN** genetic-prompt-optimizer の SKILL.md が Claude に読み込まれ、instructions に従った処理が開始される

#### Scenario: /rl-loop でスキルが呼び出される
- **WHEN** ユーザーが `/rl-loop` と入力する
- **THEN** rl-loop-orchestrator の SKILL.md が Claude に読み込まれ、instructions に従った処理が開始される

### Requirement: SKILL.md instructions の書き換え

SKILL.md の instructions を「ユーザー向けドキュメント」から「Claude への実行指示」に書き換える。Claude がスクリプトを自動的に実行する形式とする。

#### Scenario: /optimize 実行時に Claude がスクリプトを自動実行する
- **WHEN** `/optimize --target .claude/skills/my-skill/SKILL.md` が呼び出される
- **THEN** Claude は instructions に従い、`python3 <PLUGIN_DIR>/skills/genetic-prompt-optimizer/scripts/optimize.py --target .claude/skills/my-skill/SKILL.md` を自動実行する

#### Scenario: /optimize に引数が指定されない場合
- **WHEN** `/optimize` が引数なしで呼び出される
- **THEN** Claude は instructions に従い、ユーザーに `--target` の指定を求める

#### Scenario: /rl-loop 実行時に Claude がスクリプトを自動実行する
- **WHEN** `/rl-loop --target .claude/skills/my-skill/SKILL.md` が呼び出される
- **THEN** Claude は instructions に従い、`python3 <PLUGIN_DIR>/skills/rl-loop-orchestrator/scripts/run-loop.py --target .claude/skills/my-skill/SKILL.md` を自動実行する

### Requirement: instructions に含めるべき要素

SKILL.md の instructions は以下の要素を MUST で含む。

#### Scenario: 引数パース手順が記述されている
- **WHEN** SKILL.md の instructions を確認する
- **THEN** ユーザーの入力から `--target`, `--generations`, `--population`, `--fitness`, `--dry-run`, `--restore` 等の引数を解釈する手順が記述されている

#### Scenario: スクリプト実行コマンドが記述されている
- **WHEN** SKILL.md の instructions を確認する
- **THEN** `python3 <PLUGIN_DIR>/skills/.../scripts/optimize.py` の実行コマンドが記述されている（`<PLUGIN_DIR>` はプレースホルダー）

#### Scenario: 結果の表示手順が記述されている
- **WHEN** SKILL.md の instructions を確認する
- **THEN** スクリプト実行後の結果をユーザーに分かりやすく表示する手順が記述されている

### Requirement: 既存の引数互換性

書き換え後も optimize.py / run-loop.py の既存引数はすべてサポートする。

#### Scenario: --dry-run が利用可能
- **WHEN** `/optimize --target PATH --dry-run` が呼び出される
- **THEN** optimize.py に `--dry-run` が渡され、LLM 呼び出しなしの構造テストが実行される

#### Scenario: --fitness が利用可能
- **WHEN** `/optimize --target PATH --fitness skill_quality` が呼び出される
- **THEN** optimize.py に `--fitness skill_quality` が渡され、指定した適応度関数で評価される

#### Scenario: --restore が利用可能
- **WHEN** `/optimize --target PATH --restore` が呼び出される
- **THEN** optimize.py に `--restore` が渡され、バックアップから復元される
