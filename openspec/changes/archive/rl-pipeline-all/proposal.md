## Why

「手動版RLAnything」のボトルネックを解消する。現在、開発中の教訓はすべて人間が言語化して MEMORY.md/skills/rules に反映しているが、このステップが律速になっている。スキルの遺伝的最適化、自律進化ループの2層を導入し、教訓の抽出→反映を半自動化する。

## What Changes

- 遺伝的プロンプト最適化 Skill を新規作成（LLM でスキルのバリエーション生成→適応度関数で評価→進化）
- 自律進化ループオーケストレーター Skill + 採点エージェントを新規作成（実装者・採点者・進化者の3役分担）
- プロジェクト固有の適応度関数を `scripts/rl/fitness/` に配置

## Capabilities

### New Capabilities
- `rl-genetic-skill-optimizer`: スキル/ルールの遺伝的最適化フレームワーク。バリエーション生成（突然変異 + 交叉）、適応度関数による評価、世代管理、バックアップ/復元
- `rl-autonomous-loop`: 3役分担型の自律進化ループ。ベースライン取得→バリエーション生成→評価→選択→人間確認の1サイクルを自動化
- `rl-scorer-agent`: 技術品質 + ドメイン品質 + 構造評価の統合スコアを算出する採点エージェント
- `rl-fitness-functions`: プロジェクト固有の適応度関数（汎用・ルールベース・カスタム）

### Modified Capabilities
（なし）

## Impact

- **新規ディレクトリ**: `skills/genetic-prompt-optimizer/`, `skills/rl-loop-orchestrator/`
- **新規エージェント**: `agents/rl-scorer.md`
- **依存**: `claude` CLI（headless mode）

## Origin

atlas-breeaders プロジェクトで設計・実装され、rl-anything plugin として独立した。
Layer 1（rl-session-log-analyzer: claude-reflect 拡張）は atlas-breeaders 固有のため除外。
