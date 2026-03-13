## Why

evolve.py で skill_evolve/discover の結果を remediation に注入する際、ソース関数の返り値構造を読まずに変換コードを書き、フィールド名が全て不一致になるバグが発生した。この種の「モジュール間データ契約の不整合」は汎用的な検証知見であり、rl-anything が evolve した全プロジェクトに展開できるべきもの。現状、検証知見をプロジェクト横断で管理・提案する仕組みがない。

## What Changes

- **検証知見カタログ**: プラグイン内に汎用的な検証知見（verification knowledge）のカタログを新設し、discover/evolve で対象プロジェクトに PJ 固有ルールとして提案する
- **RECOMMENDED_ARTIFACTS 統合**: 既存の RECOMMENDED_ARTIFACTS に `detection_fn` フィールドを追加し、検証知見カタログエントリを動的マージ。コードパターン検出 → ルール提案を行う
- **コード構造分析**: discover フェーズで import グラフや dict 変換パターンを静的解析し、検証知見の適用可否を判定する
- **evolve 経由の自動展開**: evolve が走ったプロジェクトに対して、該当する検証ルールを自動提案

## Capabilities

### New Capabilities
- `verification-catalog`: 汎用検証知見のカタログ管理とプロジェクトへの展開メカニズム
- `code-pattern-detection`: モジュール間統合パターン（dict変換・glueコード）の静的検出

### Modified Capabilities
- `RECOMMENDED_ARTIFACTS`: `detection_fn: Optional[str]` フィールド追加、検証知見カタログエントリの動的マージ

## Impact

- `skills/discover/scripts/discover.py` — RECOMMENDED_ARTIFACTS 拡張、コードパターン検出統合
- `scripts/lib/issue_schema.py` — VERIFICATION_RULE_CANDIDATE 定数 + factory 追加
- `skills/evolve/scripts/evolve.py` — 検証知見提案フェーズの追加
- `skills/evolve/scripts/remediation.py` — `verification_rule_candidate` issue type 追加
- プラグイン内テンプレート — 検証知見カタログ（初期エントリ: data-contract-verification）
