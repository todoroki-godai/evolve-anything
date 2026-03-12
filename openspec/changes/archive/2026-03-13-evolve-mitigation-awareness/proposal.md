## Why

evolve の推奨アクション（Step 10.2）はテレメトリの問題件数が閾値を超えたら一律で提案を出すが、既に hook/rule/skill で対策済みかを確認しない。結果として「sleep パターン → run_in_background 推奨」のように、対策済みの問題が毎回レポートされる。

#26 で global scope の rule/hook 自動提案（remediation）は整備済み。本 change はその上に積む Step 10.2 の表示改善であり、推奨 → 対策のマッピングを導入して対策済みなら「対策済み（検出件数 N）」に切り替えることで、evolve レポートの信号対雑音比を改善する。#26

## What Changes

- discover.py の既存 `RECOMMENDED_ARTIFACTS` を拡張し、各エントリに `recommendation_id`（Step 10.2 との紐付け）と `content_patterns`（hook 内容チェック用）を追加
- `detect_installed_artifacts()` の返却を拡張し、content_pattern ベースの対策検証と条件別メトリクスを返す
- tool_usage_analyzer.py に閾値定数を追加（BUILTIN_THRESHOLD, SLEEP_THRESHOLD, BASH_RATIO_THRESHOLD）
- evolve SKILL.md Step 10.2 を更新: 対策済みなら検出件数を表示、未対策なら従来通り提案

## Capabilities

### New Capabilities
- `mitigation-awareness`: 推奨アクション → 対策マッピング、対策存在チェック（RECOMMENDED_ARTIFACTS 拡張）、条件別メトリクス

### Modified Capabilities
- `tool-usage-analysis`: `detect_installed_artifacts()` の返却に mitigation 検証結果を追加。`analyze_tool_usage()` 自体には `mitigation_status` を追加しない（SSOT: discover 側で統合）

## Impact

- `skills/discover/scripts/discover.py` — RECOMMENDED_ARTIFACTS 拡張、detect_installed_artifacts 拡張
- `scripts/lib/tool_usage_analyzer.py` — 閾値定数追加、check_artifact_installed() 汎用化
- `skills/evolve/SKILL.md` — Step 10.2 の表示条件分岐を更新
