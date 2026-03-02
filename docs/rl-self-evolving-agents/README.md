# 自己進化するAIエージェント - 調査レポート

> 調査日: 2026-03-01
> 動機: [RLAnything解説動画](https://www.youtube.com/watch?v=SwwTkZmbHrs)の内容をAtlas Breeadersの開発プロセスに取り入れたい

## 概要

AIコーディングエージェント（特にClaude Code）に強化学習的な自己進化の仕組みを組み込む実践事例とベストプラクティスの調査。

## 目次

| ファイル | 内容 |
|---------|------|
| [rlanything-paper.md](./rlanything-paper.md) | RLAnything論文の詳細・核心技術 |
| [claude-code-practices.md](./claude-code-practices.md) | Claude Codeでの自己進化の実践事例（5つのリポジトリ） |
| [academic-landscape.md](./academic-landscape.md) | 学術的な最新動向（Self-Rewarding, Meta-Rewarding等） |
| [implementation-patterns.md](./implementation-patterns.md) | 実装パターン5種 + 落とし穴と対策 |
| [atlas-breeaders-analysis.md](./atlas-breeaders-analysis.md) | 本プロジェクトへの適用分析 |
| [claude-reflect-deep-dive.md](./claude-reflect-deep-dive.md) | claude-reflect 2リポジトリの詳細比較 |

## 結論（先に）

1. **このプロジェクトは「手動版RLAnything」をすでに回している**（MEMORY.md + スキル作成）
2. **ボトルネックは「人間が教訓を言語化して書く」ステップ**
3. **PostToolUseフック + Stopフック自動リフレクション**で最も低コストに自動化可能
4. 既存の23スキル + 11ルール + OpenSpecワークフローとの相性は非常に良い
