## ADDED Requirements

### Requirement: Avoidance-count based graduation
pitfall_hygiene() は回避回数ベースで卒業候補を判定する（SHALL）。pitfall がトリガーされずにスキルが N 回実行された場合に卒業候補とする。N はスキルの実行頻度に応じて動的調整する。

| 実行頻度スコア | 卒業閾値 N |
|---------------|-----------|
| 3（日常的） | 10回 |
| 2（週数回） | 5回 |
| 1（月数回） | 3回 |

#### Scenario: High frequency skill graduation
- **WHEN** 日常的に使用されるスキル（頻度スコア3）の Active pitfall が10回連続でトリガーされなかった
- **THEN** 卒業候補として表示される

#### Scenario: Low frequency skill graduation
- **WHEN** 月数回使用のスキル（頻度スコア1）の Active pitfall が3回連続でトリガーされなかった
- **THEN** 卒業候補として表示される

#### Scenario: Avoidance count reset on trigger
- **WHEN** pitfall が回避カウント中にトリガーされた
- **THEN** Avoidance-count がリセットされ、Last-seen が更新される

### Requirement: Active pitfall cap enforcement
自己進化済みスキルの Active pitfall が10件を超えた場合、剪定レビューを提案する（SHALL）。

#### Scenario: Active cap exceeded
- **WHEN** あるスキルの Active pitfall が11件になった
- **THEN** evolve の Housekeeping ステージで「Active pitfall 11件 — 剪定レビューを推奨」と表示し、卒業候補をリストする

#### Scenario: Graduation candidates sorted by avoidance count
- **WHEN** 剪定レビューが提案された
- **THEN** 卒業候補は Avoidance-count 降順で表示される（最も長く回避されたものが先頭）

### Requirement: Stale knowledge guard
Last-seen が6ヶ月以上前の Active pitfall に警告を付与する（SHALL）。

#### Scenario: Stale pitfall warning
- **WHEN** Active pitfall の Last-seen が6ヶ月以上前である
- **THEN** 「Stale: 最終確認から6ヶ月超 — 現在も有効か検証を推奨」マーカーを付与する

#### Scenario: Stale pitfall in pre-flight
- **WHEN** Stale マーカー付きの pitfall が Pre-flight で読み込まれた
- **THEN** 「注意: この pitfall は6ヶ月以上前の知見です。外部仕様変更の可能性があります」と併記される

### Requirement: Cross-skill root cause aggregation
pitfall_hygiene() は全自己進化済みスキルの pitfalls を横断走査し、同一根本原因カテゴリの集中を検出する（SHALL）。

#### Scenario: Root cause concentration detected
- **WHEN** 3つ以上のスキルで `tool_use` カテゴリの Active pitfall がある
- **THEN** Report に「tool_use カテゴリの問題が3件のスキルに分散 — 共通ルール化を検討」と表示する

#### Scenario: No concentration
- **WHEN** 根本原因カテゴリが各スキルに分散しており、集中パターンがない
- **THEN** 横断分析のセクションは「問題なし」と表示する

### Requirement: Insufficient telemetry data handling
テレメトリデータが不足している場合、安全にフォールバックする（SHALL）。

#### Scenario: No usage data for skill
- **WHEN** usage.jsonl に対象スキルのレコードが存在しない
- **THEN** 実行頻度スコアを1（最低）として扱い、卒業閾値を3回（最小）に設定する。「テレメトリデータ不足: 最小閾値で判定」と注記する

#### Scenario: Insufficient data for graduation
- **WHEN** スキルの総実行回数が卒業閾値 N 未満である
- **THEN** 卒業判定をスキップし、「データ不足: 実行回数 M/N — 卒業判定には追加データが必要」と表示する

### Requirement: Evolved skill status in report
evolve の Report に自己進化済みスキルのステータスサマリを追加する（SHALL）。

#### Scenario: Report with evolved skills
- **WHEN** 3つのスキルが自己進化済みである
- **THEN** Report に以下を表示:
  - 自己進化済みスキル数
  - 各スキルの pitfall 統計（Active/New/Candidate/Graduated 件数）
  - 剪定推奨があればフラグ
  - 根本原因カテゴリの横断分析結果
