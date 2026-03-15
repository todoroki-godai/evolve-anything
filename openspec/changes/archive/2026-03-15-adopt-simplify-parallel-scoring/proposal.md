## Why

rl-scorer は現在1つの sonnet エージェントが3軸（技術・ドメイン・構造）を同時評価しているが、Claude Code v2.1.63 の `/simplify` が示した「専門エージェント並列パターン」を適用することで、評価精度の向上とコスト削減を同時に実現できる。また、evolve パイプラインの Compile ステージで remediation が Python コードを自動修正した際、コード品質のチェックが構造検証（regression gate）のみで不十分。

## What Changes

- rl-scorer を3つの専門サブエージェント（haiku×3）の並列実行に分解し、結果を統合するアーキテクチャに変更
  - technical-scorer: 技術品質軸に特化（明確性・完全性・一貫性・エッジケース・テスト可能性）
  - domain-scorer: ドメイン品質軸に特化（CLAUDE.md ドメイン推定 + ドメイン固有評価）
  - structural-scorer: 構造品質軸に特化（フォーマット・長さ・例示・参照・規約準拠）
- 既存の重み配分（技術40%・ドメイン40%・構造20%）と出力フォーマット（0.0-1.0 統合スコア + JSON）は維持
- evolve の Compile ステージ完了後、remediation がファイル変更した場合のみ `/simplify` を実行する条件付きゲートを追加
- `/simplify` 未実行環境（古い Claude Code）では従来通り regression gate のみで動作（後方互換）

## Capabilities

### New Capabilities
- `parallel-scoring`: rl-scorer の3並列サブエージェント化（haiku×3）。評価精度向上 + コスト削減
- `simplify-gate`: evolve Compile ステージ後の /simplify 条件付き品質ゲート

### Modified Capabilities

## Impact

- `agents/rl-scorer.md`: サブエージェント起動・結果統合のオーケストレーション構造に変更
- `skills/evolve/SKILL.md`: Step 5.5 後に /simplify ゲートステップ追加
- `skills/rl-loop-orchestrator/SKILL.md`: scoring 呼び出し部分の変更（並列エージェント対応）
- rl-loop, evolve-fitness など rl-scorer を利用する全スキルに影響（ただしインターフェース不変のため透過的）
