## MODIFIED Requirements

### Requirement: Auto-fixable action execution with rationale
auto_fixable カテゴリの問題に対して、修正アクションと修正理由（rationale）を生成し、AskUserQuestion で一括承認を求める（SHALL）。承認後に修正を実行する。全レイヤー（stale_ref, stale_rule, claudemd_phantom_ref, claudemd_missing_section）の auto_fixable issue に対して FIX_DISPATCH テーブル経由で対応する fix 関数を呼び出す（SHALL）。FIX_DISPATCH には既存の `fix_stale_references` も `"stale_ref"` として統合する（SHALL）。

#### Scenario: Batch approval with rationale display
- **WHEN** auto_fixable な問題が3件検出された（stale_ref 1件、claudemd_phantom_ref 2件を含む）
- **THEN** 3件をまとめて表示し、各修正に対する rationale を併記した上で、AskUserQuestion で「一括修正」「スキップ」を選択させる

#### Scenario: User skips auto-fix
- **WHEN** ユーザーが auto_fixable の一括修正をスキップした
- **THEN** 修正は実行されず、次のカテゴリの処理に進む。user_decision = "skipped" が記録される

#### Scenario: Fix dispatch for new layer types
- **WHEN** auto_fixable に `stale_rule` issue が含まれている
- **THEN** `FIX_DISPATCH["stale_rule"]` で `fix_stale_rules()` が呼び出される

#### Scenario: Fix dispatch for unknown type
- **WHEN** auto_fixable に FIX_DISPATCH に登録されていない issue type が含まれている
- **THEN** 当該 issue はスキップされ、warning がログ出力される

### Requirement: Proposable action generation with explanation
proposable カテゴリの問題に対して、具体的な修正案と「なぜこの修正が必要か」「なぜこの方法か」の説明を生成する（SHALL）。全レイヤー（line_limit_violation, near_limit, orphan_rule, stale_memory, memory_duplicate）の proposable issue に対応する（SHALL）。

#### Scenario: Line limit violation with reference extraction proposal
- **WHEN** スキルが行数制限を超過している
- **THEN** reference ファイルへの切り出し提案と理由を生成し、ユーザーに提示する

#### Scenario: orphan_rule deletion proposal
- **WHEN** `orphan_rule` が proposable に分類されている
- **THEN** ルール削除の提案と理由（「どのスキル・CLAUDE.md からも参照されていません」）をユーザーに提示する

#### Scenario: stale_memory update proposal
- **WHEN** `stale_memory` が proposable に分類されている
- **THEN** エントリの更新/削除提案と理由をユーザーに提示する

#### Scenario: memory_duplicate merge proposal
- **WHEN** `memory_duplicate` が proposable に分類されている
- **THEN** セクション統合の提案と理由（類似度表示付き）をユーザーに提示する

#### Scenario: User approves proposable fix
- **WHEN** ユーザーが proposable な修正案を承認した
- **THEN** 修正を実行し、結果を表示する。user_decision = "approved" が記録される

#### Scenario: User rejects proposable fix
- **WHEN** ユーザーが proposable な修正案を却下した
- **THEN** 修正は実行されず、user_decision = "rejected" と却下パターンが記録される

### Requirement: classify_issue() の auto_fixable 条件に project scope を追加
classify_issue() は `confidence >= 0.9 AND scope in ("file", "project")` を auto_fixable の条件としなければならない（MUST）。global scope のみ manual_required とする。これにより CLAUDE.md 修正（claudemd_phantom_ref, claudemd_missing_section）が auto_fixable に正しく分類される。

#### Scenario: project scope の issue が auto_fixable に分類される
- **WHEN** confidence >= 0.9 かつ scope == "project" の issue が classify_issue() に渡された
- **THEN** category = "auto_fixable" に分類される

#### Scenario: global scope の issue は manual_required のまま
- **WHEN** confidence >= 0.9 かつ scope == "global" の issue が classify_issue() に渡された
- **THEN** category = "manual_required" に分類される
