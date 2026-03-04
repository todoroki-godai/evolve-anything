## ADDED Requirements

### Requirement: /audit レポートに品質推移セクションを含めなければならない（MUST）

audit.py の generate_report() は、高頻度 global スキルの品質スコア履歴を読み込み、品質推移セクション（"## Skill Quality Trends"）をレポートに含めなければならない（MUST）。品質推移セクションには各スキルのスコア履歴をスパークライン風（Unicode ブロック文字）で視覚化しなければならない（SHALL）。

#### Scenario: 品質推移セクションの表示

- **WHEN** `/rl-anything:audit` を実行し、quality-baselines.jsonl に commit スキルの計測レコードが 5 件存在する
- **THEN** レポートに "## Skill Quality Trends" セクションが含まれ、commit スキルのスコア推移がスパークライン風に表示される

#### Scenario: quality-baselines.jsonl が存在しない場合

- **WHEN** `/rl-anything:audit` を実行し、quality-baselines.jsonl が存在しない
- **THEN** 品質推移セクションは表示されない（既存のレポートセクションのみ出力）

#### Scenario: 計測レコードが1件のみの場合

- **WHEN** quality-baselines.jsonl にあるスキルの計測レコードが 1 件のみ
- **THEN** そのスキルの現在のスコアのみ表示し、推移グラフは表示しない

### Requirement: 劣化検知時に警告と /optimize 推奨をレポートに表示しなければならない（MUST）

品質推移セクションにおいて劣化が検知されたスキルには、警告マーカーと /optimize 推奨コマンドを表示しなければならない（MUST）。劣化なしのスキルには警告マーカーを表示してはならない（MUST NOT）。

#### Scenario: 劣化スキルの警告表示

- **WHEN** commit スキルのスコアが 0.85 から 0.74 に低下し劣化と判定されている
- **THEN** レポートの品質推移セクションに commit スキルの行に "DEGRADED" 警告マーカーと推奨コマンド "/optimize commit" が表示される
- **FORMAT**: `commit  ▁▃▅▇▅▃ 0.74 DEGRADED → /optimize commit`

#### Scenario: 劣化なしスキルの正常表示

- **WHEN** openspec-refine スキルのスコアがベースラインから 5% 以内の変動に収まっている
- **THEN** レポートの品質推移セクションに openspec-refine スキルの行に警告マーカーは表示されない
- **FORMAT**: `openspec-refine  ▃▅▅▇▇ 0.82`

### Requirement: 品質推移セクションに再スコアリングが必要なスキルを表示しなければならない（SHALL）

前回計測から使用回数 50 回以上または 7 日以上経過したスキルを "RESCORE NEEDED" として表示しなければならない（SHALL）。

#### Scenario: 再スコアリングが必要なスキルの表示

- **WHEN** commit スキルの前回計測から 8 日経過している
- **THEN** レポートの品質推移セクションに commit スキルの行に "RESCORE NEEDED" マーカーが表示される

#### Scenario: 再スコアリングが不要なスキルの表示

- **WHEN** openspec-refine スキルの前回計測から 2 日前で使用回数差分が 10
- **THEN** レポートの品質推移セクションに "RESCORE NEEDED" マーカーは表示されない
