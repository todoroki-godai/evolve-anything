## ADDED Requirements

### Requirement: Corrections-based pitfall extraction
corrections.jsonl からスキル関連の修正パターンを抽出し、pitfall Candidate として自動追加する（SHALL）。抽出対象は `correction_type` が `stop` または `iya` で、`last_skill` が設定されているレコードとする（SHALL）。`last_skill` が null または空文字列のレコードは抽出対象外とする（SHALL）。

#### Scenario: Correction with skill context creates Candidate
- **WHEN** corrections.jsonl に `correction_type: "stop"`, `last_skill: "narrative-ux-writing"`, `message: "先送り表現を検出しました"` のレコードがある
- **THEN** `narrative-ux-writing` スキルの `references/pitfalls.md` に Root-cause を抽出した Candidate が追加される

#### Scenario: Duplicate correction is deduplicated
- **WHEN** 同一 `last_skill` + 類似 `message`（Jaccard ≥ ROOT_CAUSE_JACCARD_THRESHOLD）の correction が複数ある
- **THEN** 既存 Candidate の Occurrence-count が加算され、閾値到達時に New に昇格する

#### Scenario: Correction without skill context is skipped
- **WHEN** corrections.jsonl に `last_skill: null` のレコードがある
- **THEN** pitfall 抽出の対象外とする

### Requirement: Error-log supplementary detection
errors.jsonl から頻出エラーパターンを検出し、corrections ベースの Candidate を補強する（SHALL）。同一エラーが ERROR_FREQUENCY_THRESHOLD 回以上出現した場合に Candidate として追加する（SHALL）。errors.jsonl が存在しない場合は corrections のみで実行を継続する（SHALL）。

#### Scenario: Frequent error creates Candidate
- **WHEN** errors.jsonl に同一スキルで同一エラーメッセージ（Jaccard ≥ 0.5）が 3 回以上記録されている
- **THEN** 対象スキルの pitfalls.md に Candidate が追加される

#### Scenario: Infrequent error is ignored
- **WHEN** エラーが 2 回以下しか発生していない
- **THEN** pitfall 候補として追加されない

### Requirement: Malformed record handling
パース不可能な corrections/errors レコードに遭遇した場合、該当レコードをスキップして処理を継続する（SHALL）。スキップしたレコード数をログに記録する（SHALL）。

#### Scenario: Malformed correction message
- **WHEN** corrections.jsonl に JSON パース不可能なレコードが含まれている
- **THEN** 該当レコードをスキップし、残りのレコードで正常に処理を継続する

#### Scenario: Missing errors.jsonl
- **WHEN** errors.jsonl が存在しない
- **THEN** corrections のみで pitfall 自動検出を実行する

#### Scenario: Empty last_skill skipped
- **WHEN** corrections.jsonl に `last_skill: ""` （空文字列）のレコードがある
- **THEN** pitfall 抽出の対象外とする（null と同様に扱う）

### Requirement: Auto-detection integration with discover
discover の実行時に corrections/errors ベースの pitfall 自動検出を統合する（SHALL）。`run_discover()` の結果に `pitfall_candidates` フィールドを追加する。

#### Scenario: Discover reports pitfall candidates
- **WHEN** `/rl-anything:discover` が実行された
- **THEN** 結果に `pitfall_candidates: [{skill_name, root_cause, source, count}]` が含まれる
