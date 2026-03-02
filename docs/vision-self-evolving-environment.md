# Vision: Claude Code 環境の自律進化エンジン

> 作成日: 2026-03-02
> ステータス: 設計方針（実装前）

## ビジョン

**rl-anything は「Claude Code 環境を、使えば使うほど賢くする」プラグイン。**

skills / rules / memory / CLAUDE.md の全ライフサイクル（発見 → 生成 → 最適化 → 淘汰）を管理し、
環境全体が自律的に適応・進化する。

## 現状 → 目指す姿

```
現状:                              目指す姿:
  /optimize  (スキル最適化)          /evolve   (全ライフサイクル)
  /rl-loop   (自律最適化ループ)      /audit    (健康診断)
  /generate-fitness (評価関数生成)    /discover (パターン発見)
                                     /optimize (最適化 — 既存拡張)
                                     /prune    (淘汰)
                                     /feedback (フィードバック収集)
                                     /evolve-fitness (評価関数の自己成長)
```

## アーキテクチャ

```
┌─────────────────────────────────────────────────────┐
│  Sensors（入力）                                     │
│                                                     │
│  環境観測 hooks ──→ usage / errors / sessions .jsonl │
│  最適化観測 ──→ strategy tags / CoT reasons /        │
│                  rejection reasons (telemetry)       │
│  claude-reflect ──→ learnings-queue / CLAUDE.md      │
│  ユーザーFB ──→ /feedback → GitHub Issues            │
│                                                     │
└──────────────────────┬──────────────────────────────┘
                       │ ファイル経由
                       ▼
┌─────────────────────────────────────────────────────┐
│  rl-anything Core                                    │
│                                                     │
│  Observe → Discover → Optimize → Prune → Report     │
│                                                     │
│  全変更は人間承認が必要                               │
└─────────────────────────────────────────────────────┘
```

## 5フェーズ概要

| Phase | 何をするか | 詳細 |
|-------|-----------|------|
| **Observe** | 使用状況を静かに記録。async hooks + 最適化テレメトリ | [observe.md](./evolve/observe.md) |
| **Discover** | 観測データからスキル/ルール候補を発見 | [discover.md](./evolve/discover.md) |
| **Optimize** | 遺伝的アルゴリズムでスキル/ルールの品質改善 | [optimize.md](./evolve/optimize.md) |
| **Prune** | 未使用・重複・dead glob のアーティファクトを淘汰 | [prune.md](./evolve/prune.md) |
| **Report** | 環境の健康状態を1画面レポート + クロスラン集計 | [report.md](./evolve/report.md) |

## 対象アーティファクト

| アーティファクト | Discover | Create | Optimize | Prune |
|-----------------|----------|--------|----------|-------|
| `.claude/skills/*/SKILL.md` | ✅ | ✅ | ✅ | ✅ アーカイブ |
| `.claude/rules/*.md` | ✅ | ✅ | ✅ | ✅ アーカイブ |
| `CLAUDE.md` | — | — | ✅ 圧縮 | — |
| `memory/*.md` | — | — | ✅ 圧縮 | — |

## 運用モデル

```
日常:  何もしない。hooks が裏で観測データを蓄積（コスト: ゼロ）
日次:  /evolve → 1画面レポート → 人間が承認/却下（2-5分）
随時:  /optimize, /audit, /prune, /feedback を個別実行
```

観測データが少ない場合（前回から3セッション未満）は `/evolve` が自動スキップを提案。

## スコープ: Global vs Project

**デフォルトは project スコープのみ操作。**

| 操作 | project | global |
|------|---------|--------|
| Discover / Create | ✅ 自動 | 提案のみ（`--scope global` で明示） |
| Optimize | ✅ 自動 | `--scope global` で明示 |
| Prune | ✅ 自動 | ❌ 操作しない |
| Audit | ✅ 含む | ✅ 含む（読み取りのみ） |

詳細は [bloat-control.md](./evolve/bloat-control.md#global-vs-project-スコープ) を参照。

## claude-reflect との関係

**センサーとして利用。なくても動く。**

rl-anything が claude-reflect のファイルを一方的に読むだけ。
claude-reflect 未インストールでも動作する（入力ソースが1つ減るだけ）。

## 横断的関心事

| 関心事 | 何をするか | 詳細 |
|--------|-----------|------|
| **評価関数の自己成長** | accept/reject・rejection_reason から評価関数を自動改善 | [fitness-evolution.md](./evolve/fitness-evolution.md) |
| **肥大化制御** | skills/rules/memory/CLAUDE.md の膨張をコードで構造的に制約 | [bloat-control.md](./evolve/bloat-control.md) |
| **スコープ最適化** | Usage Registry + Scope Advisor で global/project の最適配置を提案 | [bloat-control.md](./evolve/bloat-control.md#evolve-の解決策-3層アプローチ) |

## 関連ドキュメント

- [設計原則 + 落とし穴](./evolve/principles.md)
- [段階的実装計画](./evolve/implementation-plan.md)
- [評価関数の自己成長](./evolve/fitness-evolution.md)
- [肥大化制御](./evolve/bloat-control.md)
- [既存リサーチ](./rl-self-evolving-agents/README.md)
