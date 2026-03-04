## ADDED Requirements

### Requirement: detect_contradictions performs LLM-based contradiction detection
`detect_contradictions()` は `claude -p` を使用して corrections リスト内の矛盾するペアを検出する（MUST）。LLM 呼び出し失敗時は空リストを返し、stderr に警告を出力する（MUST）。

#### Scenario: Contradictory corrections detected
- **WHEN** corrections に「常に日本語で応答して」と「Always respond in English」が含まれる
- **THEN** 矛盾ペアとして `[{"pair": [index_a, index_b], "reason": "言語指示が矛盾"}]` を返す

#### Scenario: No contradictions
- **WHEN** corrections に矛盾するペアがない
- **THEN** 空リストを返す

#### Scenario: LLM call fails
- **WHEN** `claude -p` がタイムアウトまたはエラーを返す
- **THEN** 空リストを返し、stderr に `"Warning: contradiction detection failed"` を出力する

#### Scenario: Empty corrections input
- **WHEN** 空リストまたは 1 件以下の corrections が渡される
- **THEN** LLM を呼び出さず即座に空リストを返す

### Requirement: reflect.py invokes detect_contradictions
`reflect.py` は corrections の処理時に `detect_contradictions()` を呼び出す（MUST）。呼び出しがなければ dead code となり矛盾検出が機能しない。

#### Scenario: reflect calls detect_contradictions
- **WHEN** `reflect.py` が corrections リストを処理する
- **THEN** `detect_contradictions(corrections)` を呼び出し、矛盾ペアがあればユーザーに警告を表示する

### Requirement: validate_corrections fallback defaults to safe side
`validate_corrections()` の LLM 失敗時フォールバックは `is_learning=False` とする（MUST）。フォールバック発動時は stderr に警告を出力する（MUST）。

#### Scenario: LLM failure fallback
- **WHEN** `semantic_analyze()` が例外を投げた
- **THEN** 全 correction に `is_learning=False` を設定し、stderr に `"Warning: validate_corrections failed, defaulting to is_learning=False"` を出力する

#### Scenario: Count mismatch fallback
- **WHEN** `semantic_analyze()` の結果件数が入力件数と一致しない
- **THEN** 全 correction に `is_learning=False` を設定し、stderr に `"Warning: validate_corrections count mismatch (expected N, got M), defaulting to is_learning=False"` を出力する

### Requirement: Optimizer scoring fallback emits warning
`_execution_evaluate()` が test-tasks 未設定で 0.5 を返す場合、および `_parse_cot_response()` がパース失敗で 0.5 を返す場合に、stderr に警告を出力する（MUST）。

#### Scenario: No test tasks configured
- **WHEN** `--test-tasks` が未指定で `_execution_evaluate()` が呼ばれる
- **THEN** stderr に `"Warning: no test-tasks configured, execution score defaults to 0.5"` を出力し、0.5 を返す

#### Scenario: CoT response parse failure
- **WHEN** LLM レスポンスが JSON でもスコア数値でもない
- **THEN** stderr に `"Warning: CoT response parse failed, score defaults to 0.5"` を出力し、0.5 を返す

### Requirement: Dry-run scores are clearly marked
dry-run モードのスコアリング結果にはユーザーが区別できるマーカーを含める（MUST）。

#### Scenario: Dry-run baseline score
- **WHEN** `get_baseline_score(dry_run=True)` が呼ばれる
- **THEN** 返却値の `summary` に `[dry-run]` が含まれる

#### Scenario: Dry-run variant comparison output
- **WHEN** dry-run モードでバリエーション比較結果を表示する
- **THEN** 出力に `"注意: dry-run モードのスコアは実際の品質を反映しません"` を含める

### Requirement: Dead LLM code in backfill/analyze.py is removed
`backfill/analyze.py` の `semantic_validate()` は LLM を呼ばず prompt テンプレートを返すだけで、戻り値も未使用の dead code であるため削除する（MUST）。

#### Scenario: semantic_validate removed
- **WHEN** `backfill/analyze.py` が実行される
- **THEN** `semantic_validate()` 関数が存在せず、`run_analysis()` は `semantic_detector.py` の `validate_corrections()` のみを使用する

### Requirement: get_baseline_score production fallback emits warning
`get_baseline_score()` が production パス（非 dry-run）で LLM 失敗時に 0.50 を返す場合、stderr に警告を出力する（MUST）。

#### Scenario: Production LLM failure fallback
- **WHEN** `get_baseline_score(dry_run=False)` で LLM 呼び出しが失敗する
- **THEN** 0.50 を返し、stderr に `"Warning: baseline scoring failed, defaulting to 0.50"` を出力する

### Requirement: _load_workflow_hints warns on stats-only JSON
`_load_workflow_hints()` が stats-only JSON（ワークフローヒントなし）に遭遇して空文字列を返す場合、stderr に警告を出力する（MUST）。

#### Scenario: Stats-only JSON input
- **WHEN** `workflows.jsonl` が stats のみでワークフローヒントを含まない
- **THEN** 空文字列を返し、stderr に `"Warning: no workflow hints found in stats-only data"` を出力する
