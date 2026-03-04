## ADDED Requirements

### Requirement: 高頻度 global/plugin スキルの品質スコアを計測し quality-baselines.jsonl に記録しなければならない（MUST）

quality_monitor.py は高頻度スキル（直近 HIGH_FREQ_DAYS（デフォルト: 30）日で HIGH_FREQ_THRESHOLD（デフォルト: 10）回以上使用）のうち、classify_artifact_origin() が "global" または "plugin" を返すスキルを対象に、CoT 付き品質評価（clarity / completeness / structure / practicality の4軸）を実行し、結果を ~/.claude/rl-anything/quality-baselines.jsonl に追記しなければならない（MUST）。各レコードにはスキル名・総合スコア・各軸スコア・タイムスタンプ・計測時点の使用回数を含めなければならない（MUST）。

定数定義（quality_monitor.py 先頭）:
- `HIGH_FREQ_THRESHOLD = 10` — 高頻度判定の使用回数閾値
- `HIGH_FREQ_DAYS = 30` — 高頻度判定の対象期間（日）
- `RESCORE_USAGE_THRESHOLD = 50` — 再スコアリングの使用回数閾値
- `RESCORE_DAYS_THRESHOLD = 7` — 再スコアリングの経過日数閾値
- `DEGRADATION_THRESHOLD = 0.10` — 劣化判定の低下率閾値
- `MAX_RECORDS_PER_SKILL = 100` — スキルあたりの最大レコード数

#### Scenario: 高頻度 global スキルの品質計測

- **WHEN** quality_monitor.py を実行し、usage.jsonl に直近30日で commit スキルが 60 回記録されている
- **THEN** commit スキルの SKILL.md を読み込み、CoT 評価を実行し、quality-baselines.jsonl にスコアレコードを追記する

#### Scenario: 低頻度スキルは計測対象外

- **WHEN** quality_monitor.py を実行し、usage.jsonl に直近30日で rarely-used スキルが 3 回のみ記録されている
- **THEN** rarely-used スキルの品質計測は実行しない

#### Scenario: project-scope スキルは計測対象外

- **WHEN** usage.jsonl にプロジェクトローカルスキルの使用が 100 回記録されている
- **THEN** そのスキルは classify_artifact_origin() が "project" を返すため品質計測の対象外とする

#### Scenario: plugin scope スキルは計測対象

- **WHEN** usage.jsonl に plugin scope スキルの使用が直近30日で 15 回記録されている
- **THEN** classify_artifact_origin() が "plugin" を返すため、高頻度判定を満たせば品質計測の対象とする

#### Scenario: LLM 評価がタイムアウトした場合

- **WHEN** quality_monitor.py が `claude -p` を呼び出し、タイムアウトまたはエラーが発生する
- **THEN** そのスキルの計測をスキップし、quality-baselines.jsonl の既存レコードは変更しない。エラーを stderr に出力する

### Requirement: quality_monitor.py が `claude -p` を直接呼び出し品質評価を実行しなければならない（MUST）

quality_monitor.py は optimize.py の `_llm_evaluate()` と同一のプロンプトを使用して `claude -p` を直接呼び出し、CoT 品質評価を実行しなければならない（MUST）。optimize.py のリファクタリングは行わない（MUST NOT）。評価基準（clarity / completeness / structure / practicality、各25%）は optimize.py と同一のプロンプトテンプレートを使用しなければならない（SHALL）。

#### Scenario: quality_monitor.py の品質評価が optimize と同一の基準を使用する

- **WHEN** quality_monitor.py で commit スキルを評価する
- **THEN** optimize.py の `_llm_evaluate()` と同一のプロンプトで `claude -p` を呼び出し、clarity / completeness / structure / practicality の4軸で各25%の重みで評価し、0.0〜1.0 の総合スコアと各軸の score/reason を返す

### Requirement: quality-baselines.jsonl のレコード形式が規定のスキーマに従わなければならない（MUST）

各レコードは以下のフィールドを含まなければならない（MUST）: skill_name (string), score (float, 0.0-1.0), criteria (dict: clarity/completeness/structure/practicality の各 score/reason), timestamp (ISO 8601), usage_count_at_measure (int), skill_path (string)。

スキルあたりのレコード数が MAX_RECORDS_PER_SKILL（デフォルト: 100）を超えた場合、最も古いレコードから削除しなければならない（MUST）。

#### Scenario: レコードスキーマの検証

- **WHEN** quality_monitor.py が品質計測を完了する
- **THEN** 追記されるレコードに skill_name, score, criteria, timestamp, usage_count_at_measure, skill_path の全フィールドが含まれる

#### Scenario: criteria フィールドの構造

- **WHEN** 品質計測レコードの criteria フィールドを参照する
- **THEN** clarity, completeness, structure, practicality の各キーに score (float) と reason (string) が含まれる

#### Scenario: レコード数上限の適用

- **WHEN** commit スキルの既存レコードが 100 件あり、新たに品質計測が実行される
- **THEN** 最も古いレコードを1件削除してから新しいレコードを追記し、レコード数は 100 件を維持する
