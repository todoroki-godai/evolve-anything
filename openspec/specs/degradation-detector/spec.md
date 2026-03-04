## ADDED Requirements

### Requirement: 使用回数閾値または期間閾値で再スコアリングをトリガーしなければならない（MUST）

degradation detector は高頻度 global/plugin スキルごとに、前回の品質計測からの使用回数が RESCORE_USAGE_THRESHOLD（デフォルト: 50）回以上、または前回の品質計測から RESCORE_DAYS_THRESHOLD（デフォルト: 7）日以上経過した場合に再スコアリングをトリガーしなければならない（MUST）。どちらか一方の条件を満たせばトリガーする（SHALL）。

#### Scenario: 使用回数による再スコアリングトリガー

- **WHEN** commit スキルの前回計測時の usage_count_at_measure が 100 で、現在の使用回数が 155 の場合
- **THEN** 差分 55 >= 50 であるため、再スコアリングがトリガーされる

#### Scenario: 使用回数が閾値未満で再スコアリング不要

- **WHEN** commit スキルの前回計測時の usage_count_at_measure が 100 で、現在の使用回数が 130 の場合
- **THEN** 差分 30 < 50 であるため、使用回数トリガーは発火しない

#### Scenario: 期間による再スコアリングトリガー

- **WHEN** openspec-refine スキルの前回計測 timestamp が 10 日前の場合
- **THEN** 経過日数 10 >= 7 であるため、再スコアリングがトリガーされる

#### Scenario: 両方の条件が閾値未満

- **WHEN** 前回計測から 3 日経過、使用回数の差分が 20 の場合
- **THEN** どちらの条件も満たさないため、再スコアリングはトリガーされない

### Requirement: ベースラインからのスコア低下が DEGRADATION_THRESHOLD 以上の場合に劣化と判定しなければならない（MUST）

degradation detector は各スキルの品質履歴における最高スコア（ベースライン）と直近スコアの移動平均を比較し、DEGRADATION_THRESHOLD（デフォルト: 0.10、10%）以上の低下がある場合に劣化と判定しなければならない（MUST）。移動平均は直近3回の計測スコアから算出しなければならない（SHALL）。

#### Scenario: 10% 以上のスコア低下で劣化検知

- **WHEN** commit スキルのベースライン（最高スコア）が 0.85 で、直近3回の移動平均が 0.74 の場合
- **THEN** 低下率 (0.85 - 0.74) / 0.85 = 12.9% >= 10% であるため、劣化と判定される

#### Scenario: 10% 未満のスコア低下では劣化と判定しない

- **WHEN** commit スキルのベースライン（最高スコア）が 0.85 で、直近3回の移動平均が 0.80 の場合
- **THEN** 低下率 (0.85 - 0.80) / 0.85 = 5.9% < 10% であるため、劣化とは判定されない

#### Scenario: 計測履歴が3回未満の場合

- **WHEN** 品質計測レコードが 2 件しかない場合
- **THEN** 存在する計測スコアの平均をそのまま使用し、ベースラインと比較する

#### Scenario: 初回計測ではベースライン記録のみ

- **WHEN** あるスキルの品質計測レコードが存在しない場合
- **THEN** 今回の計測結果をベースラインとして記録し、劣化判定は行わない

### Requirement: 劣化検知時に /optimize 推奨通知を生成しなければならない（MUST）

劣化が検知されたスキルに対して、/optimize コマンドの実行を推奨する通知メッセージを生成しなければならない（MUST）。通知にはスキル名・現在のスコア・ベースラインスコア・低下率・推奨コマンドを含めなければならない（SHALL）。

#### Scenario: 劣化検知時の推奨通知

- **WHEN** commit スキルのスコアが 0.85 から 0.74 に低下し劣化と判定される
- **THEN** 通知メッセージに skill_name: "commit"、current_score: 0.74、baseline_score: 0.85、decline_rate: 12.9%、recommended_command: "/optimize commit" が含まれる

#### Scenario: 複数スキルの同時劣化検知

- **WHEN** commit と openspec-refine の両方で劣化が検知される
- **THEN** 両方のスキルについてそれぞれ個別の推奨通知が生成される

### Requirement: 再スコアリング結果を quality-baselines.jsonl に追記しなければならない（MUST）

再スコアリングの結果は新しいレコードとして quality-baselines.jsonl に追記しなければならない（MUST）。既存レコードの上書きや削除は行わない（MUST NOT）。

#### Scenario: 再スコアリング結果の追記

- **WHEN** 再スコアリングトリガーにより commit スキルの品質計測が実行される
- **THEN** 新しいスコアレコードが quality-baselines.jsonl の末尾に追記され、既存レコードは変更されない
