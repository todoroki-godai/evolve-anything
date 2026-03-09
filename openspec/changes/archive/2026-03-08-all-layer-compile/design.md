Related: #16

## Context

evolve の Compile ステージは現在 Skill レイヤーの修正のみを実行する:
- **optimize**: corrections → Skill パッチ生成 + regression gate
- **remediation**: audit collect_issues() の結果（line_limit_violation, stale_ref, duplicate, hardcoded_value, near_limit）を分類して修正
- **reflect**: corrections → メモリルーティング

Phase 2（all-layer-diagnose）で `diagnose_all_layers()` が全レイヤーの問題リストを統一フォーマットで出力するようになったが、remediation の fix/verify は Skill レイヤー（stale_ref, line_limit_violation）しか対応しておらず、`orphan_rule`, `stale_rule`, `stale_memory`, `memory_duplicate`, `claudemd_phantom_ref`, `claudemd_missing_section` に対する修正アクションが存在しない。

### 現在の Compile フロー

```
collect_issues() → classify_issues() → auto_fixable/proposable/manual_required
                                          ↓
                        fix_stale_references() ← 唯一の auto_fix
                        generate_proposals()   ← line_limit / near_limit のみ
                        verify_fix()           ← stale_ref / line_limit のみ
```

## Goals / Non-Goals

**Goals:**
- 全レイヤーの issue type に対する fix 関数を remediation.py に追加する
- 全レイヤーの issue type に対する verify 関数を remediation.py に追加する
- generate_proposals() を全レイヤーの proposable issue に対応させる
- check_regression() を全レイヤー対応に拡張する
- evolve SKILL.md の Compile ステージ記述を更新する

**Non-Goals:**
- confidence_score / impact_scope の算出ロジック変更（Phase 2 で対応済み）
- rationale テンプレートの追加（Phase 2 で対応済み）
- classify_issues() のカテゴリ閾値変更（ただし D6 の scope 拡張は実施する）
- Subagents レイヤーの修正（観測データ不十分）
- hooks_unconfigured の自動修正（hooks 設定の自動生成は危険性が高い）

## Decisions

### D1: fix 関数をレイヤー別に追加し、dispatch パターンで呼び出す

**決定**: `fix_stale_rules()`, `fix_claudemd_phantom_refs()`, `fix_claudemd_missing_section()` を個別関数として追加。既存の `fix_stale_references()` と同じインターフェースで `[{"issue": ..., "original_content": ..., "fixed": bool, "error": ...}]` を返す。

呼び出しは `FIX_DISPATCH: Dict[str, Callable]` テーブルで issue type → fix 関数にマッピングする。既存の `fix_stale_references()` も `"stale_ref": fix_stale_references` として FIX_DISPATCH に統合する。

**代替案**: 1つの `fix_all()` 関数で全 type を処理 → 却下。関数が肥大化し、テスト困難になる。

**理由**: 各レイヤーの修正ロジックは独立しており、個別テストが容易。dispatch テーブルにより新 type の追加も容易。

### D2: fix 関数のスコープを「確実に安全な修正」に限定する

**決定**: auto_fixable（confidence >= 0.9, scope in ("file", "project")）な修正のみ fix 関数で実行する（D6 参照）:
- `stale_rule`: 参照先が不存在のルール → ルール内の参照行を削除
- `claudemd_phantom_ref`: 存在しないスキル/ルールの言及行 → 行を削除
- `claudemd_missing_section`: スキルセクション欠落 → セクションヘッダを追加

以下は proposable（提案のみ、ユーザー承認後に実行）:
- `orphan_rule`: 孤立ルール（意図的な場合あり）→ 削除提案
- `stale_memory`: 陳腐化メモリ → 更新/削除提案
- `memory_duplicate`: 重複セクション → 統合提案

以下は fix 対象外（manual_required）:
- `hooks_unconfigured`: hooks 設定の自動生成は危険

**理由**: confidence_score ベースの分類と一致。高信頼度かつ file/project scope の修正のみ自動実行し（D6 参照）、不確実な修正はユーザー判断に委ねる。

### D3: verify_fix() を VERIFY_DISPATCH テーブルで dispatch する

**決定**: `verify_fix()` も `VERIFY_DISPATCH: Dict[str, Callable]` テーブルで issue type → verify 関数にマッピングする。FIX_DISPATCH と対称設計にすることで、新 type 追加時に fix/verify の両方を一貫して拡張できる。検証方法:
- `stale_rule`: 修正後のルールファイルから当該パス参照が消えているか
- `claudemd_phantom_ref`: 修正後の CLAUDE.md から当該スキル/ルール名言及が消えているか
- `claudemd_missing_section`: 修正後の CLAUDE.md にスキルセクションが存在するか
- `orphan_rule`（proposable 承認後）: ルールファイルが削除されているか
- `stale_memory`: 当該行が削除/更新されているか
- `memory_duplicate`: Jaccard 係数が閾値未満に下がっているか

**代替案**: if/elif で分岐 → 却下。FIX_DISPATCH と非対称になり、type 追加時に修正箇所が分散する。

**理由**: FIX_DISPATCH と同じ dispatch パターンを採用することで、fix/verify のペアが一目で確認でき、新 type 追加時の漏れを防ぐ。

### D4: check_regression() の全レイヤー拡張

**決定**: 既存の check_regression()（見出し構造保持、コードブロック対応、空ファイルチェック）はすべての Markdown ファイルに適用可能なため、変更不要。ただし Rules ファイル（3行以内制約）用に行数チェックを追加する。

**理由**: Rules は `.claude/rules/` 下の `.md` ファイルで、`line_limit.py` の `MAX_RULE_LINES` 定数で定義された行数以内の制約がある。修正後にこの制約を超過していないかの検証が必要。

### D5: evolve SKILL.md の Compile ステージ更新

**決定**: Compile ステージの記述に「全レイヤー診断結果 → remediation への渡し方」を追記する。`collect_issues()` は内部で `diagnose_all_layers()` を統合済み（audit.py 行1137-1148）のため、別途マージする必要はない。SKILL.md には `collect_issues()` を remediation に渡す手順を明記する。

**理由**: evolve はスキルプロンプト駆動であり、SKILL.md の記述が実行フローを決定する。

### D6: classify_issue() の auto_fixable 条件に project scope を追加

**決定**: `classify_issue()` の auto_fixable 条件を `confidence >= 0.9 AND scope == "file"` から `confidence >= 0.9 AND scope in ("file", "project")` に緩和する。global scope のみ引き続き manual_required とする。

**代替案1**: spec 側を proposable に修正 → 却下。claudemd_phantom_ref / claudemd_missing_section は高信頼度で安全な修正であり、proposable にする必要がない。
**代替案2**: compute_impact_scope() の戻り値を変更 → 却下。CLAUDE.md は project scope が正しく、scope 判定を歪めるべきではない。

**理由**: auto_fixable は無確認で実行するのではなく AskUserQuestion で一括承認を求めるため、project scope でも安全性は確保されている。CLAUDE.md 修正（phantom_ref / missing_section）は project scope だが auto_fixable として扱うのが妥当。

## Risks / Trade-offs

- **[Risk] orphan_rule 削除の偽陽性** → proposable カテゴリ（ユーザー承認必須）で緩和。自動削除しない
- **[Risk] CLAUDE.md の phantom_ref 行削除で前後の文脈が壊れる** → check_regression() の見出し構造チェックで検出。行削除後に空リスト項目が残らないよう連続空行を正規化
- **[Risk] stale_rule の参照行削除でルールが3行以内制約を超過する（ルール内容の意味が変わる）** → verify_fix() + check_regression() で行数チェック
- **[Trade-off] hooks_unconfigured を manual_required に留める** → 安全性を優先。hooks 設定の自動生成は意図しない副作用のリスクが高い
- **[Trade-off] memory_duplicate の統合は自動化しない** → セマンティックな判断が必要で LLM 支援なしでは品質が担保できない。proposable として提案のみ
