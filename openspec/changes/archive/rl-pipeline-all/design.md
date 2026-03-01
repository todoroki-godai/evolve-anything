## Context

スキル/ルールの改善サイクルを半自動化する。遺伝的最適化（Layer 2）と自律進化ループ（Layer 3）の2層構成で、教訓の反映を効率化する。

```
Layer 2: 遺伝的最適化 (genetic-prompt-optimizer)
    ↓ バリエーション生成→評価→選択
Layer 3: 自律ループ (rl-loop-orchestrator + rl-scorer)
    ↓ 3役分担で1サイクル自動化
```

## Goals / Non-Goals

**Goals:**
- Layer 2: スキルファイルの遺伝的最適化を Skill として実装する
- Layer 3: 評価→進化の1ループを人間確認付きで自動化する
- 全レイヤーを独立したコンポーネントとして実装し、段階的に利用可能にする

**Non-Goals:**
- 完全自律ループ（人間確認ステップは必須）
- 外部フレームワーク（DSPy, Promptfoo 等）の統合
- API コスト最適化（初期は最小パラメータで運用）

## Decisions

### 1. 配布形態: Claude Code Plugin

**選択**: Plugin として独立リポジトリに分離し、他プロジェクトで再利用可能にする
**代替案**: (a) 各プロジェクトに直接配置 (b) hooks として実装
**理由**: Skill なら `/optimize` で呼び出せ、Plugin として配布すれば複数プロジェクトで利用可能。

### 2. 評価モデル: 3軸統合スコア

**選択**: 技術品質 (40%) + ドメイン品質 (40%) + 構造品質 (20%) の3軸
**代替案**: 単一スコア
**理由**: 技術品質とドメイン品質は独立した軸。ドメイン品質は CLAUDE.md からドメインを自動推定して評価軸を切替。

### 3. 遺伝的最適化の LLM 利用

**選択**: `claude` CLI（headless mode）で突然変異・交叉・評価を実行
**代替案**: Anthropic API を直接呼び出す
**理由**: Skill 内から `claude -p` で呼び出す方が認証管理が不要で、既存の Claude Code 環境をそのまま活用できる。

### 4. 適応度関数の分離

**選択**: 汎用フレームワーク（Plugin）+ 固有適応度関数（`scripts/rl/fitness/`）に分離
**代替案**: すべて Plugin 内に含める
**理由**: 適応度関数はプロジェクト固有。分離することで Plugin を他プロジェクトでも再利用可能。

## Risks / Trade-offs

| リスク | 軽減策 |
|--------|--------|
| API コスト | 初期パラメータ最小限（集団3×世代3） |
| モデル崩壊（全バリエーションが劣化） | 元スキルを baseline として保持。全劣化時は不採用 |
| rl-scorer の評価精度 | 人間確認ステップを必須にし、完全自動化を避ける |

## 後続 Changes

この設計を拡張する active changes:
- **evaluate-pipeline-upgrade**: CoT評価、Pairwise比較、実行ベース評価、Regression Gate、失敗パターン自動蓄積
- **generate-fitness-skill**: プロジェクト固有 fitness 関数の自動生成
- **skill-ux-and-readme**: スラッシュコマンド UX、README ストーリー
