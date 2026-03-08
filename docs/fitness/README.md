# Fitness 評価 — 環境全体の適応度測定

Claude Code 環境（CLAUDE.md / Rules / Skills / Memory / Hooks / Subagents）**全体**の適応度をどう評価するかの調査・設計ドキュメント。

## 背景

現状の fitness は Skill テキストのキーワードマッチのみ（`scripts/rl/fitness/plugin.py`）。
PJ ごとに「成功」の定義が異なり、Skill だけでなく全6レイヤーの相互作用が品質を決める。

## 評価の3つの問い

| 問い | 内容 | PJ依存 |
|------|------|--------|
| Q1: 整ってる？ | 構造的整合性 + カバレッジ（静的分析） | 共通 |
| Q2: 効いてる？ | テレメトリ + 利用率 + ablation（間接測定） | 共通 |
| Q3: 成功する？ | タスク実行 + 成否判定（直接測定） | PJ固有 |

## ドキュメント

| ファイル | 内容 |
|---------|------|
| [evaluation-patterns.md](evaluation-patterns.md) | 10パターンの詳細設計（P1-P10）+ 調査知見 |
| [phased-approach.md](phased-approach.md) | 比較表 + フェーズドアプローチ（Phase 0-3） |
| [implementation-plan.md](implementation-plan.md) | 実装計画 — 既存資産の活用、各 Phase の具体タスク、判断基準 |

## フェーズドアプローチ概要

| Phase | 内容 | コスト | パターン |
|-------|------|--------|---------|
| 0 | 構造の整合性チェック | ゼロ | P4 Coherence + P9 KG Quality |
| 1 | テレメトリ駆動の効果測定 | ゼロ | P5 Telemetry + P8 Kirkpatrick + P10 Implicit |
| 2 | 原則ベースの自動評価 | LLM | P3 Constitutional + P7 Chaos |
| 3 | タスク実行による成果測定 | LLM | P1 Task Exec + P2 Eureka + P6 Elo |

## 関連

- [GitHub Issue #15](https://github.com/todoroki-godai/evolve-anything/issues/15) — 原本（コメントで詳細）
- [Issue #14](https://github.com/todoroki-godai/evolve-anything/issues/14) — Skill 特化の PJ 固有評価（Phase 3 相当、保留中）
- [docs/evolution/](../evolution/) — 進化側（#16）— 測定結果を使って**どう改善するか**
- [roadmap.md](../roadmap.md) — 全体ロードマップ
