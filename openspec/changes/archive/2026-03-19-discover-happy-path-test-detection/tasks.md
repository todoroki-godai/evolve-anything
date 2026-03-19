Related: #33

## 1. テスト作成（TDD First）

- [x] 1.1 `detect_happy_path_test_gap()` のテストを作成: パイプライン検出+テスト欠落→applicable=True、テスト存在→applicable=False、閾値未満→applicable=False、project_dir不在→safe_result
- [x] 1.2 パイプライン検出パターンのユニットテスト: Python step_*/phase_*/stage_*/layer_*/process_* パターン、ループパターン（for step in steps）、TypeScript await チェーンパターン（camelCase: `await stepValidate()` 等）
- [x] 1.3 テストファイル対応解決のテスト: 同ディレクトリ test_*.py、ソース親の tests/ サブディレクトリ、プロジェクトルート直下の tests/、__tests__/ サブディレクトリ、*.test.ts。content-aware 検出テスト（別名ルールにキーワードが含まれる場合の導入済み判定）
- [x] 1.4 VERIFICATION_CATALOG エントリ存在テスト: happy-path-test-verification エントリの構造確認
- [x] 1.5 RECOMMENDED_ARTIFACTS エントリ存在テスト: test-happy-path-first エントリの導入済み/未導入判定

## 2. 検出関数の実装

- [x] 2.1 `_HAPPY_PATH_RULE_TEMPLATE` をルールテンプレート定数として追加
- [x] 2.2 パイプライン検出 regex パターンを定義（`_PIPELINE_CALL_PATTERN`, `_PIPELINE_LOOP_PATTERN`）。TypeScript は camelCase 対応（`await\s+(?:step|phase|stage|layer|process)\w+\(`）
- [x] 2.3 `_find_test_file()` を実装: ソースファイルに対応するテストファイルのパス解決（同ディレクトリ + ソース親 tests/ + プロジェクトルート tests/ + __tests__/）
- [x] 2.4 `_detect_pipeline_functions()` を実装: ファイル内のパイプライン関数名を検出して返す
- [x] 2.5 `detect_happy_path_test_gap()` を実装: パイプライン検出→テストファイル探索→欠落判定→evidence（ファイルパスのみ、関数名は含めない）/confidence/llm_escalation_prompt を返す
- [x] 2.6 `HAPPY_PATH_MIN_PATTERNS = 2` 閾値定数を追加

## 3. カタログ・レコメンデーション統合

- [x] 3.1 `VERIFICATION_CATALOG` に `happy-path-test-verification` エントリを追加。`_DETECTION_FN_DISPATCH` に `"detect_happy_path_test_gap": detect_happy_path_test_gap` を登録。`_CONTENT_KEYWORDS_MAP` に `"happy-path-test-verification": ["ハッピーパス", "happy path", "E2Eテスト", "正常系テスト"]` を登録
- [x] 3.2 `discover.py` の `RECOMMENDED_ARTIFACTS` に `test-happy-path-first` エントリを追加

## 4. 検証

- [x] 4.1 全テスト実行（`python3 -m pytest scripts/tests/ skills/discover/scripts/tests/ -v`）で既存テストが壊れていないことを確認
- [x] 4.2 `detect_happy_path_test_gap()` が rl-anything 自身のコードに対して正常に動作することを手動確認
