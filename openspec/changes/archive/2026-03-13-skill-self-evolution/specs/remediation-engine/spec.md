**Note**: この spec は `skill_evolve_candidate` に関する追加・変更分のみ記載する。remediation engine の既存 Requirements（Proposable action generation, Manual-required reporting 等）は変更なしのため省略。

## MODIFIED Requirements

### Requirement: Confidence-based issue classification
remediation engine は audit の検出結果を受け取り、各問題に対して `confidence_score`（修正の確実性 0.0〜1.0）と `impact_scope`（file / project / global）を算出し、閾値ベースで `auto_fixable`、`proposable`、`manual_required` の3カテゴリに動的分類する（SHALL）。confidence_score の算出時、confidence-calibration.json が存在しアクティブな場合は、キャリブレーション済みの値で静的値を上書きしなければならない（MUST）。

新規 issue type `skill_evolve_candidate` を分類対象に追加する。skill_evolve_assessment() が適性高（12-15点）と判定したスキルは confidence 0.85 で proposable に分類する。適性中（8-11点）は confidence 0.60 で proposable に分類する。

#### Scenario: High suitability skill classified as proposable
- **WHEN** skill_evolve_assessment() がスキルを適性高（13点）と判定した
- **THEN** issue type `skill_evolve_candidate` が confidence 0.85, impact_scope "project" で `proposable` に分類される

#### Scenario: Medium suitability skill classified as proposable
- **WHEN** skill_evolve_assessment() がスキルを適性中（9点）と判定した
- **THEN** issue type `skill_evolve_candidate` が confidence 0.60, impact_scope "project" で `proposable` に分類される

#### Scenario: Low suitability skill not classified
- **WHEN** skill_evolve_assessment() がスキルを適性低（5点）と判定した
- **THEN** issue は生成されない（変換非推奨のため remediation 対象外）

#### Scenario: Anti-pattern rejection overrides score
- **WHEN** スコアが10点だが評価時アンチパターンに2件該当する
- **THEN** issue は生成されない（アンチパターン検出で変換非推奨）

#### Scenario: Calibrated confidence applied
- **WHEN** confidence-calibration.json に `{stale_ref: {calibrated: 0.80, status: "active"}}` が存在する
- **THEN** stale_ref の confidence_score は 0.95（静的値）ではなく 0.80（キャリブレーション値）が使用される

#### Scenario: Calibration file missing or inactive
- **WHEN** confidence-calibration.json が存在しない、または status が "active" でない
- **THEN** 既存の静的 confidence_score がそのまま使用される

#### Scenario: High-confidence file-scoped issue classified as auto_fixable
- **WHEN** audit が通常の MEMORY ファイル内の陳腐化参照を検出した
- **THEN** confidence_score ≥ 0.9 かつ impact_scope = file と判定され、`auto_fixable` カテゴリに分類される

#### Scenario: No issues detected
- **WHEN** audit の検出結果に問題が含まれない
- **THEN** 分類処理をスキップし、空の分類結果を返す

#### Scenario: Dry-run mode
- **WHEN** evolve が `dry_run=True` で実行されている
- **THEN** 分類と提案の表示は行うが、修正の実行と outcome の記録は行わない

### Requirement: Auto-fixable action execution with rationale
auto_fixable カテゴリの問題に対して、修正アクションと修正理由（rationale）を生成し、AskUserQuestion で一括承認を求める（SHALL）。全レイヤーの auto_fixable issue に対して FIX_DISPATCH テーブル経由で対応する fix 関数を呼び出す（SHALL）。

FIX_DISPATCH に `skill_evolve_candidate` を追加する。fix 関数は evolve_skill_proposal() を呼び出し、テンプレートベースの変換を適用する。

#### Scenario: Skill evolution fix via FIX_DISPATCH
- **WHEN** `skill_evolve_candidate` issue が proposable に分類され、ユーザーが承認した
- **THEN** `FIX_DISPATCH["skill_evolve_candidate"]` で `fix_skill_evolve()` が呼び出され、自己進化パターンが適用される

#### Scenario: Batch approval with rationale display
- **WHEN** auto_fixable な問題が3件検出された
- **THEN** 3件をまとめて表示し、各修正に対する rationale を併記した上で、AskUserQuestion で承認を求める

#### Scenario: Fix dispatch for unknown type
- **WHEN** auto_fixable に FIX_DISPATCH に登録されていない issue type が含まれている
- **THEN** 当該 issue はスキップされ、warning がログ出力される

### Requirement: Verify dispatch for skill evolution
VERIFY_DISPATCH に `skill_evolve_candidate` → `verify_skill_evolve()` を追加する（SHALL）。検証内容: (1) `references/pitfalls.md` が存在すること、(2) SKILL.md に自己更新セクション（Failure-triggered Learning, Pre-flight Check）が存在すること。

#### Scenario: Successful verification
- **WHEN** `fix_skill_evolve()` で変換が適用された後に `verify_skill_evolve()` が呼び出された
- **THEN** `references/pitfalls.md` の存在と SKILL.md の自己更新セクション存在を確認し、検証成功を返す

#### Scenario: Verification failure — missing pitfalls.md
- **WHEN** 変換後に `references/pitfalls.md` が存在しない
- **THEN** 検証失敗を返し、`remediation-outcomes.jsonl` に failure として記録する

#### Scenario: Verification failure — missing sections
- **WHEN** 変換後の SKILL.md に Failure-triggered Learning セクションが存在しない
- **THEN** 検証失敗を返し、欠落セクション名を詳細に含める

### Requirement: Proposable skill evolution flow
`skill_evolve_candidate` が proposable（confidence 0.60、適性中）に分類された場合の提案生成フローを定義する（SHALL）。

#### Scenario: Medium suitability proposal generation
- **WHEN** 適性中のスキルが proposable として分類された
- **THEN** `generate_proposals()` が `skill_evolve_candidate` を受け取り、適性スコア・成長ポイント・懸念点を含む提案テキストを生成する。AskUserQuestion でユーザーに「変換する」「スキップ」の選択を求める

#### Scenario: User approves medium suitability
- **WHEN** ユーザーが適性中スキルの変換を承認した
- **THEN** `fix_skill_evolve()` が呼び出され、高適性スキルと同じ変換処理が適用される

#### Scenario: User skips medium suitability
- **WHEN** ユーザーが適性中スキルの変換をスキップした
- **THEN** `remediation-outcomes.jsonl` に skipped として記録し、次回 evolve 時に再提案する
