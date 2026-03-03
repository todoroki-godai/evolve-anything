## MODIFIED Requirements

### Requirement: プロジェクト分析にワークフロー統計と fitness-criteria.md を統合しなければならない（MUST）

analyze_project.py は、ワークフロー統計 JSON（`~/.claude/rl-anything/workflow_stats.json`）と `.claude/fitness-criteria.md` が存在する場合、それらを入力ソースとして読み込み、出力 JSON に統合しなければならない（MUST）。

#### Scenario: ワークフロー統計のマージ
- **WHEN** `~/.claude/rl-anything/workflow_stats.json` が存在する
- **THEN** analyze_project.py の出力 JSON に `workflow_stats` フィールドが追加される
- **AND** 各スキルの `consistency`, `avg_steps`, `dominant_pattern` が含まれる

#### Scenario: fitness-criteria.md の読み込み
- **WHEN** `.claude/fitness-criteria.md` が存在する
- **THEN** analyze_project.py の出力 JSON の `criteria.axes` にユーザー定義の評価軸が追加される
- **AND** `sources` 配列に `.claude/fitness-criteria.md` が含まれる

#### Scenario: fitness-criteria.md が存在しない場合
- **WHEN** `.claude/fitness-criteria.md` が存在しない
- **THEN** analyze_project.py は従来通り CLAUDE.md と rules のみから criteria を生成する

#### Scenario: ワークフロー統計が存在しない場合
- **WHEN** `~/.claude/rl-anything/workflow_stats.json` が存在しない
- **THEN** analyze_project.py の出力 JSON に `workflow_stats` フィールドは含まれない

## ADDED Requirements

### Requirement: `--ask` オプションでユーザーに品質基準を質問し保存しなければならない（MUST）

`generate-fitness --ask` を実行した場合、ユーザーに品質基準を対話的に質問し、回答を `.claude/fitness-criteria.md` に保存しなければならない（MUST）。

#### Scenario: --ask による品質基準の収集
- **WHEN** `generate-fitness --ask` を実行する
- **THEN** ユーザーに「このプロジェクトの品質基準は何ですか？」と質問する
- **AND** 回答を `.claude/fitness-criteria.md` に保存する
- **AND** 保存後、通常の fitness 関数生成フローを続行する

#### Scenario: 既存の fitness-criteria.md がある場合の --ask
- **WHEN** `.claude/fitness-criteria.md` が既に存在する状態で `generate-fitness --ask` を実行する
- **THEN** 既存の内容をユーザーに提示し、更新するか確認する
- **AND** ユーザーが更新を選択した場合、新しい内容で上書きする

#### Scenario: --ask なしで fitness-criteria.md が存在する場合
- **WHEN** `generate-fitness` を `--ask` なしで実行し、`.claude/fitness-criteria.md` が存在する
- **THEN** `.claude/fitness-criteria.md` を自動的に読み込んで fitness 関数生成に使用する
