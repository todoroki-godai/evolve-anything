## 1. 検証知見カタログモジュール + RECOMMENDED_ARTIFACTS 統合

**Prerequisites: なし**

- [x] 1.1 `scripts/lib/verification_catalog.py` を新規作成: `VERIFICATION_CATALOG` リストを定義。初期エントリ `data-contract-verification` を含む。各エントリは `id`, `type`, `description`, `rule_template`, `rule_filename`, `detection_fn`, `applicability` フィールドを持つ。閾値定数 (`DATA_CONTRACT_MIN_PATTERNS`, `DETECTION_TIMEOUT_SECONDS`, `MAX_CATALOG_ENTRIES`, `LARGE_REPO_FILE_THRESHOLD`) も定義
- [x] 1.2 `scripts/lib/issue_schema.py` に追記: `VERIFICATION_RULE_CANDIDATE = "verification_rule_candidate"` 定数 + `make_verification_rule_issue(entry, detection_result)` factory 関数を追加。既存の `TOOL_USAGE_RULE_CANDIDATE` / `SKILL_EVOLVE_CANDIDATE` と同じパターン
- [x] 1.3 `detect_verification_needs(project_dir)` を `verification_catalog.py` に実装: VERIFICATION_CATALOG を走査し、未導入 + 適用可能なエントリをリストとして返す。`applicability: always` は検出関数不要で常に候補、`conditional` は `detection_fn` を呼び出して判定。エラー/タイムアウト時は `applicable: False` を返す
- [x] 1.4 テスト: `scripts/tests/test_verification_catalog.py` — カタログ構造、導入済みチェック、検出関数の呼び出し、エラーハンドリング

## 2. コードパターン検出（data-contract-verification）

**Prerequisites: 1.1**

- [x] 2.1 `detect_data_contract_verification(project_dir)` を `verification_catalog.py` に実装: rg 優先（フォールバック: Python glob + re）でプロジェクト内の Python/TypeScript ファイルを走査。`from X import Y` + 同一ファイル内の dict/object リテラル変換パターンを検出。閾値 `DATA_CONTRACT_MIN_PATTERNS` (3) 箇所以上で `applicable: True`。permission denied はスキップ
- [x] 2.2 走査対象の制限: `node_modules/`, `.venv/`, `__pycache__/`, `.git/` を除外。ファイル数 `LARGE_REPO_FILE_THRESHOLD` (1000) 超の場合は `scripts/`, `src/`, `lib/`, `skills/` に限定。`DETECTION_TIMEOUT_SECONDS` (5秒) タイムアウト
- [x] 2.3 言語別ルールテンプレート: Python → 「ソース関数の返り値構造（dictキー・型）を Read で確認」、TypeScript → 「ソース関数の戻り型（interface/type）を Read で確認」。プロジェクトの主要言語を `.py` vs `.ts/.tsx` ファイル数で判定（同数時は Python デフォルト）
- [x] 2.4 テスト: 検出パターンのマッチ/不マッチ、閾値判定、タイムアウト、言語別テンプレート選択、rg フォールバック

## 3. discover 統合（RECOMMENDED_ARTIFACTS 拡張）

**Prerequisites: 1.1, 1.2, 1.3**

- [x] 3.1 `discover.py` の RECOMMENDED_ARTIFACTS にカタログエントリを動的マージ: `verification_catalog.py` の `VERIFICATION_CATALOG` からエントリを変換し RECOMMENDED_ARTIFACTS に追加
- [x] 3.2 `detect_recommended_artifacts()` に `detection_fn` 分岐追加: `detection_fn` があるエントリは関数呼び出しで判定、結果を evidence として付加
- [x] 3.3 discover のレポートに検証知見セクションを追加: 未導入の検証知見を一覧表示、evidence（検出箇所）付き
- [x] 3.4 テスト: discover 結果に `verification_needs` が含まれること、detection_fn 分岐の動作確認

## 4. evolve remediation 統合

**Prerequisites: 1, 2, 3**

- [x] 4.1 `evolve.py` の Phase 3.5 に verification_needs → `verification_rule_candidate` issue 変換ロジックを追加。`issue_schema.py` の `VERIFICATION_RULE_CANDIDATE` 定数と `make_verification_rule_issue()` factory を使用
- [x] 4.2 `remediation.py` に `verification_rule_candidate` のサポートを追加: `compute_confidence_score()` — detection の confidence を使用、`classify_issue()` — proposable に分類（auto_fixable にはしない）、`_RATIONALE_TEMPLATES` にテンプレート追加、`generate_rationale()` にハンドラ追加、`generate_proposals()` にハンドラ追加
- [x] 4.3 `fix_verification_rule()` を実装: `.claude/rules/{rule_filename}` にルールテンプレートを書き込む。`FIX_DISPATCH["verification_rule_candidate"]` に登録
- [x] 4.4 `_verify_verification_rule()` を実装: `.claude/rules/{rule_filename}` の存在確認。`VERIFY_DISPATCH["verification_rule_candidate"]` に登録
- [x] 4.5 テスト: confidence 算出、分類、rationale 生成、fix/verify の動作確認

## 5. SKILL.md・ドキュメント更新

**Prerequisites: 3, 4**

- [x] 5.1 `skills/evolve/SKILL.md` に検証知見提案ステップの説明を追加（Diagnose セクション）
- [x] 5.2 `skills/discover/SKILL.md` に verification_needs の表示セクションを追加

## 6. 検証

**Prerequisites: 1, 2, 3, 4, 5**

- [x] 6.1 統合テスト: `evolve.py --dry-run` で検証知見検出→提案の全フローが動作することを確認
- [x] 6.2 既存テストの非破壊確認: `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v` が全パス
- [x] 6.3 実際の rl-anything プロジェクトで evolve を実行し、`data-contract-verification` が提案されることを確認
