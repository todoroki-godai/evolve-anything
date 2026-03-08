Related: #16

## Why

evolve パイプラインの Diagnose ステージは全レイヤー（Skills + Rules + Memory + Hooks + CLAUDE.md）の問題を検出できるが、Compile ステージは Skill のパッチ生成（optimize）と Skill/Rules の audit 違反修正（remediation）しか実装されていない。Rules の orphan_rule 削除、Memory の stale_memory 修正、CLAUDE.md の phantom_ref 除去といった非 Skill レイヤーの自動修正は未対応であり、roadmap Gap 2 Phase 3 として着手する。

## What Changes

- remediation.py に全レイヤーの修正アクション（fix 関数）を追加し、auto_fixable な issue を自動修正可能にする
- proposable カテゴリの issue に対して、レイヤー別の具体的な修正案を生成する
- evolve の Compile ステージが diagnose_all_layers() の結果を remediation に渡すフローを確立する
- 修正アクション実行後の verify_fix() を全レイヤー対応に拡張する
- regression gate（check_regression）を全レイヤー対応に拡張する

## Capabilities

### New Capabilities
- `all-layer-fix-actions`: 全レイヤー（Rules/Memory/CLAUDE.md）の auto_fixable issue に対する自動修正アクション
- `all-layer-proposals`: 全レイヤーの proposable issue に対する修正案生成
- `all-layer-verify`: 全レイヤーの修正後検証（verify_fix / check_regression の拡張）

### Modified Capabilities
- `remediation-engine`: 新レイヤーの fix 関数・verify 関数を追加。既存の confidence-based 分類・rationale 生成は変更なし
- `compile-stage`: Compile ステージが diagnose_all_layers() の全結果を受け取り処理するフローを追加

## Impact

- `skills/evolve/scripts/remediation.py`: fix_* 関数群の追加、verify_fix / check_regression の拡張
- `skills/evolve/SKILL.md`: Compile ステージに全レイヤー修正の手順を追加
- `openspec/specs/remediation-engine/spec.md`: 新レイヤーの fix/verify 要件を追加
- `openspec/specs/compile-stage/spec.md`: 全レイヤーフローの要件を追加
- テスト: `skills/evolve/scripts/tests/` に全レイヤー修正のテストを追加
