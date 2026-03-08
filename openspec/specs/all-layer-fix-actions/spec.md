## ADDED Requirements

### Requirement: stale_rule の自動修正
remediation は `stale_rule` issue（ルール内の参照先ファイルが不存在）に対して、該当参照行を削除する修正アクションを実行できなければならない（MUST）。

#### Scenario: stale_rule の参照行を削除する
- **WHEN** `stale_rule` issue が auto_fixable に分類されている
- **THEN** ルールファイルから該当参照行が削除され、`{"issue": ..., "original_content": str, "fixed": true, "error": null}` が返される

#### Scenario: 削除後のルールが空になる場合
- **WHEN** stale_rule の参照行削除によりルールファイルの実質的内容が空になる
- **THEN** ファイルは削除せず空行のみのファイルとして残し、proposable としてファイル削除を提案する

### Requirement: claudemd_phantom_ref の自動修正
remediation は `claudemd_phantom_ref` issue（CLAUDE.md 内で言及されたスキル/ルールが不存在）に対して、該当行を削除する修正アクションを実行できなければならない（MUST）。

#### Scenario: phantom_ref の行を削除する
- **WHEN** `claudemd_phantom_ref` issue が auto_fixable に分類されている
- **THEN** CLAUDE.md から該当行が削除され、連続空行が正規化される

#### Scenario: 削除後に空のリスト項目が残らない
- **WHEN** phantom_ref 行の削除により前後にリスト区切りのみ残る
- **THEN** 連続する空のリスト項目行も削除される

### Requirement: claudemd_missing_section の自動修正
remediation は `claudemd_missing_section` issue に対して、CLAUDE.md にスキルセクションヘッダを追加する修正アクションを実行できなければならない（MUST）。

#### Scenario: Skills セクションを追加する
- **WHEN** `claudemd_missing_section` issue が auto_fixable に分類されている
- **THEN** CLAUDE.md の末尾に `## Skills` セクションヘッダと簡易リストが追加される

### Requirement: fix 関数は dispatch テーブルで issue type にマッピングされる
`FIX_DISPATCH` dict が issue type から fix 関数へのマッピングを提供しなければならない（MUST）。

#### Scenario: 登録済み issue type の fix を呼び出す
- **WHEN** `FIX_DISPATCH["stale_rule"]` を参照する
- **THEN** `fix_stale_rules` 関数が返される

#### Scenario: 既存の stale_ref も FIX_DISPATCH に統合されている
- **WHEN** `FIX_DISPATCH["stale_ref"]` を参照する
- **THEN** `fix_stale_references` 関数が返される

#### Scenario: 未登録 issue type は KeyError
- **WHEN** `FIX_DISPATCH` に登録されていない issue type を参照する
- **THEN** KeyError が発生する（fix 不要な type は dispatch に登録しない）

### Requirement: 全 fix 関数は統一インターフェースを返す
全ての fix 関数は `List[Dict]` を返し、各要素は `{"issue": Dict, "original_content": str, "fixed": bool, "error": str|None}` 形式でなければならない（MUST）。

#### Scenario: 修正成功
- **WHEN** fix 関数が修正に成功した
- **THEN** `fixed=True, error=None` が設定される

#### Scenario: 修正失敗
- **WHEN** fix 関数がファイル書き込みに失敗した
- **THEN** `fixed=False, error="<エラーメッセージ>"` が設定され、例外は発生しない

### Requirement: regression 検出時のロールバック
fix 関数が修正を実行した後に `check_regression()` が issues を検出した場合、`rollback_fix()` で修正前の内容に復元しなければならない（MUST）。詳細は compile-stage spec の「regression 検出時のロールバック」シナリオを参照。
