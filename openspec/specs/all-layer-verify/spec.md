## Requirements

### Requirement: stale_rule の修正後検証
verify_fix() は `stale_rule` 修正後にルールファイルから当該パス参照が消えていることを検証しなければならない（MUST）。

#### Scenario: 参照が削除されている
- **WHEN** stale_rule 修正後のルールファイルに当該パス参照が存在しない
- **THEN** `{"resolved": true, "remaining": null}` が返される

#### Scenario: 参照がまだ残っている
- **WHEN** stale_rule 修正後のルールファイルに当該パス参照がまだ存在する
- **THEN** `{"resolved": false, "remaining": "参照「{path}」がまだ存在します"}` が返される

### Requirement: claudemd_phantom_ref の修正後検証
verify_fix() は `claudemd_phantom_ref` 修正後に CLAUDE.md から当該スキル/ルール名言及が消えていることを検証しなければならない（MUST）。

#### Scenario: phantom_ref が削除されている
- **WHEN** 修正後の CLAUDE.md に当該スキル/ルール名のリスト項目が存在しない
- **THEN** `{"resolved": true, "remaining": null}` が返される

### Requirement: claudemd_missing_section の修正後検証
verify_fix() は `claudemd_missing_section` 修正後に Skills セクションが存在することを検証しなければならない（MUST）。

#### Scenario: Skills セクションが追加されている
- **WHEN** 修正後の CLAUDE.md に `## Skills` セクションが存在する
- **THEN** `{"resolved": true, "remaining": null}` が返される

### Requirement: stale_memory の修正後検証
verify_fix() は `stale_memory` 修正後に当該モジュール参照行が消えていることを検証しなければならない（MUST）。

#### Scenario: stale_memory の参照が削除されている
- **WHEN** 修正後の MEMORY.md に当該モジュール名言及が存在しない
- **THEN** `{"resolved": true, "remaining": null}` が返される

### Requirement: check_regression() の Rules 行数チェック
check_regression() は Rules ファイル（`.claude/rules/*.md`）の修正後に行数が `line_limit.py` の `MAX_RULE_LINES` 定数で定義された上限以内であることを検証しなければならない（MUST）。行数上限はハードコードせず、`MAX_RULE_LINES` 定数を参照する。

#### Scenario: 修正後も行数上限以内
- **WHEN** Rules ファイルの修正後の行数が `MAX_RULE_LINES` 以内
- **THEN** `{"passed": true, "issues": []}` が返される

#### Scenario: 修正後に行数上限超過
- **WHEN** Rules ファイルの修正後の行数が `MAX_RULE_LINES` を超過
- **THEN** `{"passed": false, "issues": ["Rules ファイルが行数制限を超過しています ({lines}行)"]}` が返される

### Requirement: VERIFY_DISPATCH テーブルによる dispatch
verify_fix() は `VERIFY_DISPATCH: Dict[str, Callable]` テーブルで issue type → verify 関数にマッピングしなければならない（MUST）。FIX_DISPATCH と対称設計とし、新 type 追加時に fix/verify のペアが一目で確認できるようにする。

#### Scenario: 登録済み issue type の verify を呼び出す
- **WHEN** `VERIFY_DISPATCH["stale_rule"]` を参照する
- **THEN** stale_rule 用の verify 関数が返される

#### Scenario: 未登録 issue type はスキップ
- **WHEN** `VERIFY_DISPATCH` に登録されていない issue type の検証が要求された
- **THEN** 検証はスキップされ、warning がログ出力される
