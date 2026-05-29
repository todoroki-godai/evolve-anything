# rl-anything — Ubiquitous Language（用語集）

このプロジェクト固有の jargon を 1 語で decode するための共有言語。
AI も人も、ここの用語を使って会話・命名・記述する（Eric Evans, DDD）。

新しい概念を導入したら **必ずここに 1 行追記する**。腐った用語集は無いより悪い。
鮮度は `scripts/lib/glossary_drift.py`（spec-keeper の update が消費）が検出する。

- **意味** は 1 行で。詳細は SPEC.md / docs/decisions/ に委譲する（重複させない）。
- **初出** は概念が最初に入った issue（`#NNN`）または ADR（`ADR-NNN`）。

| 用語 | 意味 | 初出 |
|------|------|------|
| BES | 進化探索。後ろ向きサブゴール分解(#253)と前向き進化探索(#256)の総称 | #253 |
| MemTrace | episodic 検索エラーを 3 類型に分類し event_id へ帰属する診断 | #254 |
| slop | AI 定型句。日英 10 パターンを決定論 regex で検出 | #255 |
| subgoal fitness | 候補を 5 サブゴールに分解して返す密な中間フィードバック | #253 |
| Observe hooks | LLM コストゼロで使用・エラー・修正を自動記録する hook 群 | ADR-002 |
| 直接パッチ最適化 | 遺伝的アルゴリズムでなく LLM 1 パスでパッチを当てる最適化方式 | ADR-003 |
| coherence | fitness の一種。構造的整合性 4 軸スコア | ADR-004 |
| telemetry | fitness の一種。行動実績テレメトリ 3 軸スコア | ADR-005 |
| constitutional | fitness の一種。原則ベース LLM Judge 評価 | ADR-006 |
| env_score | environment fitness の統合スコア（0.0-1.0）。growth-level の素 | ADR-004 |
| cross-PJ recall | keyword 決定論で全 PJ memory を横断検索（vector 非採用） | ADR-025 |
| pitfall-curate | PJ 非依存の pitfalls.md キュレーション（自己進化専用の manager とは別物） | ADR-026 |
| 正準フォーマット収束 | pitfalls.md を寛容パーサでなく書式収束で扱う方針（無破壊 lint） | ADR-027 |
| observability contract | 必ず surface すべき行を単一ソース `_OBSERVABILITY_BUILDERS` 化し markdown/構造化の両経路が消費する契約 | ADR-028 |
| silence ≠ evaluated | 沈黙だと「評価して該当なし」か「配線漏れ」か区別できない。該当なしでも ✓ を1行残す原則 | ADR-028 |
