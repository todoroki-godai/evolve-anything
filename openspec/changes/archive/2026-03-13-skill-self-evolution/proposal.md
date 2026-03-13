## Why

evolve パイプラインはスキルを「外から」改善する（optimize, remediation）が、スキル自身が実行中に知見を蓄積して賢くなる仕組みがない。aws-deploy と figma-to-code で手動適用した「自己進化パターン」（Pre-flight Check, pitfalls.md, Failure-triggered Learning）は実績がある（figma-to-code: 16→44 pitfalls に成長）が、どのスキルに適用すべきかの判定と変換が手動のまま。これを evolve パイプラインに統合し、適性判定→変換提案→剪定を自動化する。

## What Changes

- **Diagnose に適性判定を追加**: テレメトリ（usage/errors）3軸 + LLMキャッシュ 2軸の5項目スコアリングでスキルの自己進化適性を判定。15点満点、8点以上で適性あり
- **Compile に変換提案を追加**: 適性ありスキルに自己進化パターン（Pre-flight Check, pitfalls.md, Failure-triggered Learning, 成功パターン枠, 根本原因カテゴリ）を組み込む提案を生成。ユーザー承認で適用
- **品質ゲート導入**: pitfall 記録時に Candidate→New の2段階昇格。同一根本原因が2回出現で正式 pitfall 化。ユーザー訂正は即 Active（ゲートスキップ）
- **3層コンテキスト管理**: Hot（Pre-flight: Top 3-5件, ~500 tokens）/ Warm（エラー時オンデマンド読込）/ Cold（履歴のみファイル保存）
- **Housekeeping に pitfall 剪定を追加**: 自己進化済みスキルの pitfalls を回避回数ベースで卒業判定。Active 10件超で剪定レビュー
- **対象フィルタ**: PJ固有・グローバルのカスタムスキルのみ。プラグイン・symlink スキルは除外

## Capabilities

### New Capabilities

- `skill-evolve-assessment`: テレメトリ+LLM 5項目スコアリングによるスキル自己進化適性判定。3段階分類（高/中/低）とアンチパターン検出
- `skill-evolve-transform`: 適性ありスキルへの自己進化パターン組み込み（Pre-flight, pitfalls.md テンプレ, Failure-triggered Learning, 成功パターン, 根本原因カテゴリ, Pitfall Lifecycle）
- `pitfall-quality-gate`: pitfall 記録の品質ゲート（Candidate→New 2段階昇格, 根本原因分類, 3層コンテキスト管理）
- `pitfall-hygiene`: 自己進化済みスキルの pitfall 剪定（回避回数ベース卒業, Active 上限管理, Stale Knowledge ガード）

### Modified Capabilities

- `remediation-engine`: 自己進化変換提案を FIX_DISPATCH に追加（issue_type: `skill_evolve_candidate`）

## Impact

- 対象ファイル: `skills/evolve/scripts/evolve.py`, `skills/evolve/SKILL.md`, `skills/evolve/scripts/remediation.py`
- 新規ファイル: `scripts/lib/skill_evolve.py`（適性判定 + 変換ロジック）, `scripts/lib/pitfall_manager.py`（品質ゲート + 剪定）
- テンプレート: `skills/evolve/templates/pitfalls.md`, `skills/evolve/templates/self-evolve-sections.md`（変換時にスキルに挿入するセクション）
- 依存: `telemetry_query.py`（usage/errors クエリ）, `audit.py`（classify_artifact_origin）
- 影響範囲: evolve 実行時に全カスタムスキルを走査。変換はユーザー承認必須
