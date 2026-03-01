## 1. Layer 2: 遺伝的プロンプト最適化 Skill

- [x] 1.1 SKILL.md 作成（frontmatter + ワークフロー定義）
- [x] 1.2 optimize.py 実装（Individual, GeneticOptimizer クラス、突然変異・交叉・評価・世代管理）
- [x] 1.3 test_optimizer.py 実装（21テスト）
- [x] 1.4 generations/.gitkeep 作成
- [x] 1.5 --dry-run で3世代×3集団のループ動作確認

## 2. Layer 2: 適応度関数

- [x] 2.1 scripts/rl/fitness/ ディレクトリ作成 + __init__.py
- [x] 2.2 組み込み適応度関数（default, skill_quality）実装
- [x] 2.3 カスタム適応度関数インターフェース（stdin → 0.0-1.0）定義

## 3. Layer 3: 自律進化ループ

- [x] 3.1 rl-loop-orchestrator SKILL.md 作成
- [x] 3.2 run-loop.py 実装（ベースライン取得→バリエーション生成→評価→選択→人間確認）
- [x] 3.3 test_loop.py 実装（9テスト）
- [x] 3.4 rl-scorer エージェント定義（agents/rl-scorer.md）
- [x] 3.5 --dry-run でフルループ動作確認

## 4. 統合と仕上げ

- [x] 4.1 CLAUDE.md に RLAnything セクション追加
- [x] 4.2 rl-anything plugin として独立リポジトリに分離
