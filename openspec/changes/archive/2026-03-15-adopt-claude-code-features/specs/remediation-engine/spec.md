## MODIFIED Requirements

### Requirement: Confidence-based issue classification
remediation engine は audit の検出結果を受け取り、各問題に対して `confidence_score`（修正の確実性 0.0〜1.0）と `impact_scope`（file / project / global）を算出し、閾値ベースで `auto_fixable`、`proposable`、`manual_required` の3カテゴリに動的分類する（SHALL）。confidence_score の算出時、confidence-calibration.json が存在しアクティブな場合は、キャリブレーション済みの値で静的値を上書きしなければならない（MUST）。global scope の issue は `confidence >= PROPOSABLE_CONFIDENCE` の場合に `proposable` に昇格する（MUST）。`auto_fixable` にはならない（MUST）。

新規 issue type `skill_evolve_candidate` を分類対象に追加する。skill_evolve_assessment() が適性高（12-15点）と判定したスキルは confidence 0.85 で proposable に分類する。適性中（8-11点）は confidence 0.60 で proposable に分類する。

**追加**: evolve の skill hook（PostToolUse）から呼び出される `--quick-check` モードを regression_gate.py に追加する（MUST）。このモードではテスト実行をスキップし、構文チェック（Python syntax check）のみを行う。入力は stdin から PostToolUse イベント JSON を受け取り、`tool_input.command` から変更対象 `.py` ファイルを推定する（MUST）。出力は exit code 0/1 + stderr JSON result（`{"passed": bool, "errors": [...]}`）とする（MUST）。既存の `check_gates()` とは独立した `quick_check()` 関数として実装する（SHOULD）。

#### Scenario: High suitability skill classified as proposable
- **WHEN** skill_evolve_assessment() がスキルを適性高（13点）と判定した
- **THEN** issue type `skill_evolve_candidate` が confidence 0.85, impact_scope "project" で `proposable` に分類される

#### Scenario: Quick-check mode syntax validation
- **WHEN** regression_gate.py が `--quick-check` フラグ付きで呼び出される
- **AND** stdin から PostToolUse イベント JSON（`tool_name: "Bash"`, `tool_input.command: "python3 scripts/lib/foo.py"`）を受け取る
- **THEN** command 文字列から対象 `.py` ファイルを推定し、`py_compile.compile()` で構文チェックのみを実行する
- **AND** テストは実行しない

#### Scenario: Quick-check mode returns gate result on error
- **WHEN** `--quick-check` で構文エラーが検出される
- **THEN** exit code 1 を返し、stderr に `{"passed": false, "errors": [{"file": "scripts/lib/foo.py", "error": "SyntaxError: ..."}]}` を出力する

#### Scenario: Quick-check mode passes
- **WHEN** `--quick-check` で対象ファイルに構文エラーがない
- **THEN** exit code 0 を返し、stderr に `{"passed": true, "errors": []}` を出力する

#### Scenario: Quick-check with non-Python tool use
- **WHEN** PostToolUse イベントの tool_name が "Bash" 以外（例: "Read"）の場合
- **THEN** チェックをスキップし exit code 0 を返す
