# 評価関数の自己成長

`/generate-fitness` で作った評価関数（fitness function）を、使い続ける中で自動的に改善する仕組み。

## なぜ評価関数が劣化するのか

```
1. ドメインドリフト: PJがdocs→APIに変わっても評価軸が古いまま
2. Goodhart's Law: スコアをターゲットにした瞬間、良い指標でなくなる
   例: 見出し7個+コードブロック5個で高スコア、中身は空っぽ
3. ユーザー嗜好の変化: 最初は「網羅性」重視、慣れると「簡潔さ」重視
```

## 自己成長の3メカニズム

### 1. Accept/Reject フィードバックからの学習

optimize/rl-loop で人間が accept/reject するたびに、評価関数の精度を検証できる。

```
history.jsonl に蓄積:
  { score: 0.82, human_accepted: true }   → 評価関数が正しかった
  { score: 0.78, human_accepted: false }  → 評価関数がズレている
  { score: 0.45, human_accepted: true }   → 見逃している良さがある

相関が 0.4 以下に落ちたら:
  → 「評価関数の再キャリブレーション推奨」を警告
  → /generate-fitness の再実行を提案
```

根拠: [Auto-Rubric (arXiv:2510.17314)](https://arxiv.org/abs/2510.17314) — 70件の accept/reject ペアで
評価基準を自動抽出可能。8B モデルでフル訓練済み報酬モデルを上回る。

### 2. rejection_reason からの評価軸発見

```
rejection_reason の蓄積:
  "冗長すぎる" × 4回
  "エッジケースが足りない" × 3回
  "実行例がない" × 2回

→ 現在の評価軸に「簡潔さ」がない！
→ fitness 関数に新しい評価軸を追加提案
```

根拠: [OpenRubrics (arXiv:2510.07743)](https://arxiv.org/abs/2510.07743) —
accept と reject の対比（contrastive signal）から rubric ルールを自動抽出。

### 3. CoT reason のパターン分析

```
高スコアの reason 傾向:
  "エッジケースが網羅されている"
  "手順が具体的で再現性が高い"

低スコアの reason 傾向:
  "手順が曖昧"
  "前提条件の記述が不足"

→ 評価軸の重みを調整
  instruction_clarity: 0.25 → 0.35
  structure_quality:   0.25 → 0.15
```

根拠: [MPO (arXiv:2504.20157)](https://arxiv.org/abs/2504.20157) — メタ報酬モデルが
rubric を段階的に精緻化。最初は基準が増え、5-10回後に安定して精度が上がる。

## Goodhart's Law 対策（6つ）

| 対策 | 実装方法 |
|------|---------|
| **スコア一貫性追跡** | 同じ候補を2回評価、分散 > 0.15 なら unreliable フラグ |
| **accept/reject 相関追跡** | 直近20回の score vs accepted の相関を監視 |
| **adversarial probe** | ゲーミング候補を意図的に生成、低スコアを返すか検証 |
| **Pareto ベース選択** | 単一スコアではなく軸ごとの Pareto フロンティアで選択 |
| **ベースライン乖離ペナルティ** | 構造が大きく変わりすぎた候補にペナルティ |
| **Early stopping** | 改善率が鈍化したら `--generations` を待たず停止 |

adversarial probe の具体例:

```
/optimize --dry-run 時に:
  1. 見出し7個 + コードブロック5個 + 中身は "TODO" だけの候補を生成
  2. skill_quality.py で評価
  3. スコアがベースライン以上 → 「脆弱性検出: 構造ゲーミング」と警告
  4. anti_pattern に追加を提案
```

根拠: [REFORM (arXiv:2507.06419)](https://arxiv.org/abs/2507.06419)

## コマンド: `/evolve-fitness`

accept/reject が30件以上蓄積されたら実行可能に。

```
/rl-anything:evolve-fitness

出力例:
  Fitness calibration report:
  ─────────────────────────────
  Score-acceptance correlation: 0.38 (⚠️ low, threshold 0.50)

  Axis drift detected:
    instruction_clarity: weight 0.25 → recommended 0.35
    structure_quality:   weight 0.25 → recommended 0.15

  Missing axes (from rejection reasons):
    conciseness: "冗長すぎる" (4 rejections)

  Anti-patterns to add (from pitfalls):
    "heading_without_content" (3 occurrences)

  Actions:
    [u]pdate fitness function / [p]review changes / [s]kip
  ─────────────────────────────
```

## 実装の段階

```
Phase 1: 観測（Step 1b の telemetry）
  history.jsonl に human_accepted, rejection_reason, cot_reasons を記録

Phase 2: 検出（/evolve 実行時）
  score-acceptance 相関を計算
  rejection_reason の頻度分析
  相関低下時に警告

Phase 3: 提案（/evolve-fitness）
  評価軸の重み調整を提案
  新しい評価軸の追加を提案
  anti_pattern の追加を提案
  → 全て人間承認が必要

Phase 4: 自動進化（将来）
  GEPA で fitness prompt 自体を最適化
  → 30+ データポイントが必要
```

## 参考論文

| 論文 | 取り入れた点 |
|------|-------------|
| [Auto-Rubric](https://arxiv.org/abs/2510.17314) | accept/reject から評価基準を自動抽出 |
| [MPO](https://arxiv.org/abs/2504.20157) | メタ報酬モデルによる rubric の段階的精緻化 |
| [CARMO](https://arxiv.org/abs/2410.21545) | アーティファクトごとの動的評価基準生成 |
| [OpenRubrics](https://arxiv.org/abs/2510.07743) | contrastive signal からの rubric 自動抽出 |
| [CREAM](https://arxiv.org/abs/2410.12735) | クロスイテレーション一貫性でスコア信頼度を判定 |
| [REFORM](https://arxiv.org/abs/2507.06419) | adversarial counterexample による脆弱性検出 |
| [GEPA](https://arxiv.org/abs/2507.19457) | fitness prompt 自体の遺伝的最適化（ICLR 2026） |
| [Rubrics as Rewards](https://arxiv.org/abs/2507.17746) | 構造化 rubric による RL（CoT 評価の根拠） |
