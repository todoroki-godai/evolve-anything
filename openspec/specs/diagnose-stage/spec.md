## ADDED Requirements

### Requirement: Diagnose ステージはパターン検出と問題検出を統合する
Diagnose ステージは discover（パターン検出 + enrich 統合）、skill triage（trigger eval 生成 + アクション判定）、audit 問題検出（collect_issues）、reorganize（split 検出のみ）、全レイヤー診断（Rules / Memory / Hooks / CLAUDE.md）、**effort 未設定スキル検出**を1ステージとして実行しなければならない（MUST）。出力はレイヤー別の問題リストと候補リスト、triage 結果、**および effort 検出結果**を含まなければならない（MUST）。

#### Scenario: 全サブステップが実行される
- **WHEN** evolve が Diagnose ステージを実行する
- **THEN** discover（enrich 統合済み）、skill triage、audit 問題検出（全レイヤー診断含む）、reorganize（split 検出）、**effort 未設定検出**が順に実行され、統合された診断結果が生成される

#### Scenario: effort 検出結果が issue に変換される
- **WHEN** audit の collect_issues() が effort 未設定スキルを検出する
- **THEN** `missing_effort` 型の issue として issues リストに追加され、remediation で `MISSING_EFFORT_CANDIDATE` として処理される

#### Scenario: discover がパターン未検出でも triage は実行される
- **WHEN** discover がパターンを検出しない（usage.jsonl のデータ不足等）
- **THEN** skill triage は sessions.jsonl から独立して実行され、audit と reorganize も正常に実行される

#### Scenario: triage 結果が evolve の出力に含まれる
- **WHEN** Diagnose ステージが完了する
- **THEN** evolve.py の出力 phases に `skill_triage` キーが含まれ、`CREATE`, `UPDATE`, `SPLIT`, `MERGE`, `OK` のアクション別スキルリストが格納される

#### Scenario: triage のデータ不足で graceful degradation
- **WHEN** sessions.jsonl のセッション数が不足し eval set 生成ができない
- **THEN** triage はスキップされ、`skill_triage: {"skipped": true, "reason": "insufficient_data"}` が出力に含まれる。他のサブステップは正常に実行される

#### Scenario: triage issue が collect_issues に統合される
- **WHEN** triage が UPDATE / SPLIT / MERGE 判定を出す
- **THEN** 各判定が issue_schema 準拠の issue として collect_issues() の結果に追加される

#### Scenario: レイヤー別診断結果が evolve の出力に含まれる
- **WHEN** Diagnose ステージが完了する
- **THEN** evolve.py の出力 phases に `layer_diagnose` キーが含まれ、`rules`, `memory`, `hooks`, `claudemd` の各レイヤーの issue リストが格納される

#### Scenario: 個別レイヤー診断がエラーでも他レイヤーは実行される
- **WHEN** `diagnose_hooks()` が settings.json の読み取りに失敗する
- **THEN** `diagnose_rules()`, `diagnose_memory()`, `diagnose_claudemd()` は正常に実行され、hooks のみエラーが記録される

### Requirement: Diagnose の出力は discover に enrich の照合結果を含む
discover の出力に、既存スキルとの Jaccard 類似度照合結果（旧 enrich の機能）を含まなければならない（MUST）。照合には `scripts/lib/similarity.py` の `jaccard_coefficient` を使用しなければならない（MUST）。

#### Scenario: パターンが既存スキルに一致
- **WHEN** discover が `error_pattern: "cdk deploy failed"` を検出し、既存スキルに `aws-cdk-deploy` が存在する
- **THEN** discover の出力の `matched_skills` に `aws-cdk-deploy` が含まれ、`similarity_score` が付与される

#### Scenario: パターンが既存スキルに不一致
- **WHEN** discover が `error_pattern: "docker compose timeout"` を検出し、docker 関連スキルが存在しない
- **THEN** discover の出力の `unmatched_patterns` に当該パターンが含まれる

### Requirement: Diagnose は session-scan を実行しない
discover のテキストレベルパターンマイニング（session-scan）は実行してはならない（MUST NOT）。usage.jsonl ベースのパターン検出のみを使用しなければならない（MUST）。

#### Scenario: session-scan が呼ばれない
- **WHEN** Diagnose ステージが実行される
- **THEN** discover 内の session-scan 関連コード（テキストマイニング）は実行されない
