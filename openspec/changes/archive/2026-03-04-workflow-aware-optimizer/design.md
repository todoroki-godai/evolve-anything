## Context

Phase B で蓄積した 301 workflows の分析結果:

| 指標 | 値 |
|------|-----|
| skill-driven | 226 WF |
| team-driven | 22 WF |
| agent-burst | 53 WF |
| 3回以上使用スキル | 13 |
| 抽象パターン一貫性（平均） | ~45% |
| 使用エージェント種別 | Explore(426), general-purpose(327), Plan(55), 他10種 |

既存コンポーネント:
- `GeneticOptimizer` (optimize.py) — SKILL.md テキストの遺伝的最適化
- `rl-scorer` (agents/rl-scorer.md) — 技術品質 + ドメイン品質 + 構造品質の 3 軸採点
- `generate-fitness` — プロジェクト固有 fitness 関数の自動生成（analyze_project.py → Claude CLI → scripts/rl/fitness/{name}.py）

## Goals / Non-Goals

**Goals:**
- 既存 GeneticOptimizer の mutation プロンプトにワークフロー知見を注入し、より効果的なバリエーションを生成する
- rl-scorer にワークフロー品質の補助シグナルを追加する
- generate-fitness がワークフロー統計を参照でき、atlas-breeders のようなプロジェクト固有指標と組み合わせ可能にする
- ワークフロー統計を算出する再利用可能なスクリプトを提供する

**Non-Goals:**
- ワークフロー genome の独立管理（分析でフィードバックループが閉じないと判明）
- ワークフローシーケンスの直接的な mutation（LLM が実行時に決定する領域）
- SKILL.md への YAML ワークフロー構造定義の追加（一貫性が低く、自然言語が適切）

## Decisions

### Decision 1: ワークフロー統計を JSON ファイルで受け渡す

workflow_analysis.py がスキル別統計 JSON を出力し、optimizer / scorer / generate-fitness がそれを読む。保存先は `~/.claude/rl-anything/workflow_stats.json`（既存の `workflows.jsonl` と同じディレクトリ）。

```json
{
  "opsx:apply": {
    "workflow_count": 40,
    "abstract_patterns": {"Explore": 19, "general-purpose": 10, "Explore → general-purpose": 4},
    "consistency": 0.475,
    "avg_steps": 3.1,
    "step_std": 3.2,
    "dominant_pattern": "Explore"
  }
}
```

**代替案 A**: optimizer 内部で workflows.jsonl を直接読む → スクリプト間で分析ロジックが重複するため却下
**代替案 B**: データベース（SQLite 等）に格納 → 現行の jsonl/json ファイルベースアーキテクチャと不整合、過剰

### Decision 2: mutation ヒントはプロンプトテキストとして注入

optimize.py の `mutate()` メソッドで、mutation プロンプトにワークフロー分析から導出されたヒントを追加テキストとして注入する。

```
改善方針:
- より具体的な例を追加
- ...（既存の方針）

ワークフロー分析からの示唆:
- このスキルは 40 回実行され、Explore エージェントのみのパターンが 47.5%
- 一貫性は 0.48 で中程度。Explore → general-purpose の遷移パターンもある
- 改善案: エージェントの使い分け基準を明確にする指示を検討
```

**代替案**: 構造化された mutation 操作（add_step 等）→ フィードバックループが閉じないため却下。LLM のテキスト mutation は自然言語レベルで「エージェント戦略を明確にせよ」等の指示を出せるため、間接的に構造を改善できる。

### Decision 3: rl-scorer への統合は補助シグナルとして

rl-scorer のドメイン品質セクションに「ワークフロー効率性」を追加軸として注入。ただし主要な評価軸は変えず、ワークフロー統計がある場合のみ加算する optional な仕組み。

- ワークフロー統計 JSON が存在する場合: ドメイン品質の一部として一貫性・効率性を加味
- 存在しない場合: 従来通りの評価（後方互換）

**理由**: ワークフロー統計はプロジェクトによっては存在しない（rl-anything 未導入/データ不足）。必須にすると使えないプロジェクトが出る。

### Decision 4: generate-fitness はワークフロー統計を analyze_project.py 出力に含める

analyze_project.py の出力 JSON に `workflow_stats` フィールドを追加。これにより Claude CLI が fitness 関数生成時に:
- atlas-breeders: 「ゲーマーのワクワク度」+ ワークフロー効率性（ブラウザ操作の成功率、エージェント協調の一貫性）
- 一般プロジェクト: ドメイン評価軸 + ワークフロー一貫性

といった組み合わせの fitness 関数を自動生成できる。プロジェクト固有の指標はあくまで CLAUDE.md + rules から推定し、ワークフロー統計は補助情報。

### Decision 5: `--ask` オプションと `.claude/fitness-criteria.md` によるユーザー定義品質基準

generate-fitness に `--ask` オプションを追加。実行時にユーザーに品質基準を対話的に質問し、回答を `.claude/fitness-criteria.md` に保存する。以降の analyze_project.py 実行時にこのファイルが存在すれば自動的に読み込み、criteria の axes に反映する。

```
# .claude/fitness-criteria.md の例
## 品質基準
- ゲーマーがワクワクする表現かどうか (weight: 0.4)
- ブラウザ操作の具体性 (weight: 0.3)
- エラーハンドリングの網羅性 (weight: 0.3)
```

フロー:
1. `generate-fitness --ask` → AskUserQuestion でユーザーに品質基準を質問
2. 回答を `.claude/fitness-criteria.md` に保存
3. analyze_project.py が CLAUDE.md + rules + `.claude/fitness-criteria.md` を統合分析
4. fitness 関数生成時にユーザー定義基準が反映される

**代替案 A**: analyze_project.py の CLI 引数で基準を渡す → 毎回指定が必要で UX が悪い
**代替案 B**: CLAUDE.md に品質基準セクションを追加させる → CLAUDE.md の責務を超える。専用ファイルに分離すべき

## Risks / Trade-offs

**[ワークフロー統計の鮮度]** → workflow_analysis.py 実行時にその時点の workflows.jsonl を分析。optimize/evolve 実行時に自動で再分析する設計にし、陳腐化を防ぐ。

**[mutation ヒントの効果が測定困難]** → 既存の history.jsonl + strategy フィールドで「ワークフローヒントあり mutation」の fitness 改善幅を追跡可能。Step 6 の戦略学習と自然に統合される。

**[rl-scorer の評価軸増加による複雑性]** → optional な補助シグナルとし、統計がない場合は従来スコアにフォールバック。既存の動作を壊さない。
