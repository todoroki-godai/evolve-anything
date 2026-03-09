Related: #16

## 1. Fix 関数の追加

- [x] 1.1 `fix_stale_rules()` を remediation.py に追加（ルール内の不存在パス参照行を削除）
- [x] 1.2 `fix_claudemd_phantom_refs()` を remediation.py に追加（CLAUDE.md 内の phantom_ref 行を削除 + 連続空行正規化）
- [x] 1.3 `fix_claudemd_missing_section()` を remediation.py に追加（Skills セクションヘッダを追加）
- [x] 1.4 `FIX_DISPATCH` テーブルを追加（issue type → fix 関数マッピング。既存の `fix_stale_references` を `"stale_ref"` として含む）

## 2. Proposals の全レイヤー対応

- [x] 2.1 `generate_proposals()` に orphan_rule の削除提案を追加
- [x] 2.2 `generate_proposals()` に stale_memory の更新/削除提案を追加
- [x] 2.3 `generate_proposals()` に memory_duplicate の統合提案を追加

## 3. Verify / Regression の全レイヤー対応

- [x] 3.1 `VERIFY_DISPATCH` テーブルを追加（issue type → verify 関数マッピング、FIX_DISPATCH と対称設計）
- [x] 3.2 `verify_fix()` に stale_rule 検証ロジックを追加
- [x] 3.3 `verify_fix()` に claudemd_phantom_ref 検証ロジックを追加
- [x] 3.4 `verify_fix()` に claudemd_missing_section 検証ロジックを追加
- [x] 3.5 `verify_fix()` に stale_memory 検証ロジックを追加
- [x] 3.6 `check_regression()` に Rules 行数チェックを追加（`line_limit.py` の `MAX_RULE_LINES` 定数を参照）

## 4. classify_issue() の scope 拡張

- [x] 4.1 `classify_issue()` の auto_fixable 条件を `confidence >= 0.9 AND scope in ("file", "project")` に変更
- [x] 4.2 単体テスト: project scope の issue が auto_fixable に分類されることを検証
- [x] 4.3 単体テスト: global scope の issue が manual_required のままであることを検証

## 5. Issue 統合フロー

- [x] 5.1 evolve SKILL.md の Compile ステージ記述を更新（`collect_issues()` が内部で `diagnose_all_layers()` を統合済みであることを明記）

## 6. テスト

- [x] 6.1 fix_stale_rules / fix_claudemd_phantom_refs / fix_claudemd_missing_section の単体テスト
- [x] 6.2 FIX_DISPATCH テーブルの dispatch テスト
- [x] 6.3 VERIFY_DISPATCH テーブルの dispatch テスト
- [x] 6.4 generate_proposals() の全レイヤー対応テスト
- [x] 6.5 verify_fix() の全レイヤー対応テスト
- [x] 6.6 check_regression() の Rules 行数チェックテスト
- [x] 6.7 既存テストの通過確認（`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v`）
