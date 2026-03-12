## 1. RECOMMENDED_ARTIFACTS 拡張（discover.py）

- [x] 1.1 `RECOMMENDED_ARTIFACTS` の `avoid-bash-builtin` エントリに `recommendation_id: "builtin_replaceable"` と `content_patterns: ["REPLACEABLE"]` を追加
- [x] 1.2 `sleep-polling-guard` エントリを新規追加（`recommendation_id: "sleep_polling"`, `content_patterns: [r"\bsleep\b"]`, hook_path は check-bash-builtin.py を共有）
- [x] 1.3 テスト: RECOMMENDED_ARTIFACTS の recommendation_id 付きエントリが必須フィールドを持つことを検証

## 2. check_artifact_installed() 汎用化（tool_usage_analyzer.py）

- [x] 2.1 `check_artifact_installed(artifact, ...)` 関数を実装 — hook_path/path の存在チェック + content_patterns の正規表現マッチ
- [x] 2.2 テスト: hook ファイルあり + content_pattern マッチ → installed=True/content_matched=True、ファイルなし → installed=False、ファイルあり + content_pattern 不一致 → installed=False/content_matched=False、I/O エラー → installed=False/content_matched=None

## 3. detect_installed_artifacts() 拡張（discover.py）

- [x] 3.1 `detect_installed_artifacts()` で recommendation_id 付きエントリに `mitigation_metrics` を付加。tool_usage_patterns から条件別メトリクスを算出（builtin: recent_count、sleep: recent_count）
- [x] 3.2 テスト: installed artifact に mitigation_metrics が含まれ、mitigated/recent_count が正しい

## 4. 閾値定数化（tool_usage_analyzer.py）

- [x] 4.1 `BUILTIN_THRESHOLD=10`, `SLEEP_THRESHOLD=20`, `BASH_RATIO_THRESHOLD=0.40`, `COMPLIANCE_GOOD_THRESHOLD=0.90` を定数として追加
- [x] 4.2 テスト: 定数が期待値であることを検証

## 5. evolve SKILL.md 更新

- [x] 5.1 Step 10.2 を更新: `installed_artifacts` の mitigation_metrics を参照し、対策済み → 検出件数表示、未対策 → 従来提案
- [x] 5.2 閾値をモジュール定数名で記述（ハードコード値ではなく定数参照）
- [x] 5.3 全対策済みかつ検出ゼロの1行表示ルールを追加

## 6. 既存テスト確認

- [x] 6.1 `python3 -m pytest scripts/lib/tests/test_tool_usage_analyzer.py -v` が pass
- [x] 6.2 `python3 -m pytest skills/discover/ -v` が pass（discover テストがあれば）
