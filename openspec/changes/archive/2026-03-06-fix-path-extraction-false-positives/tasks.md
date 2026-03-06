## 1. 既知ディレクトリプレフィックス定数と2セグメントフィルタの追加

- [x] 1.1 `skills/audit/scripts/audit.py` のモジュールレベル（既存定数付近）に `KNOWN_DIR_PREFIXES` 定数を定義する。含めるもの: `skills`, `scripts`, `hooks`, `.claude`, `openspec`, `docs`。
- [x] 1.2 `_extract_paths_outside_codeblocks()` 内に2セグメントフィルタロジックを追加する: 既存フィルタの後（405行目付近）で、セグメントが正確に2つでどちらのセグメントにもファイル拡張子がない相対パスの場合、最初のセグメントが `KNOWN_DIR_PREFIXES` に含まれなければ除外する。
- [x] 1.3 既存フィルタ（長さ、http、スラッシュコマンド、全大文字）が変更なく機能することを確認する。

## 2. 専用ユニットテストの作成

- [x] 2.1 `_extract_paths_outside_codeblocks()` のパラメータ化テストケースを含む `skills/audit/scripts/tests/test_path_extraction.py` を作成する。
- [x] 2.2 真陽性テストケースを追加: `skills/update`（既知プレフィックス、拡張子なし）、`scripts/reflect_utils.py`（既知プレフィックス、拡張子あり）、`config/settings.yaml`（未知プレフィックス、拡張子あり）、`skills/audit/scripts`（3セグメント以上）、`/Users/foo/bar`（絶対パス）、`scripts/rl/tests/test_workflow_analysis.py`（深いパス、拡張子あり）。
- [x] 2.3 偽陽性テストケースを追加: `usage/errors`（未知プレフィックス、拡張子なし）、`discover/audit`（未知プレフィックス、リストコンテキスト内で拡張子なし）、`observe/hooks`（説明的略記）。
- [x] 2.4 エッジケーステストケースを追加: コードブロック内のパスは除外される、実際のパスと説明的表現が混在するコンテンツ。

## 3. テストスイート全体の実行と検証

- [x] 3.1 `python3 -m pytest skills/audit/scripts/tests/test_path_extraction.py -v` を実行し、すべての新規テストが通過することを確認。
- [x] 3.2 `python3 -m pytest skills/audit/scripts/tests/test_collect_issues.py -v` を実行し、既存のインテグレーションテストが通過することを確認。
- [x] 3.3 `python3 -m pytest skills/evolve/scripts/tests/test_remediation.py -v` を実行し、remediation テストが通過することを確認。
- [x] 3.4 プロジェクト全体のテストスイート `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v` を実行し、リグレッションがないことを確認。
