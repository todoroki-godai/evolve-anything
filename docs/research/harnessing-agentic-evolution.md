# tech-eval: Harnessing Agentic Evolution (arXiv:2605.13821)

- **評価日**: 2026-05-15
- **対象**: Zhang et al., "Harnessing Agentic Evolution" (10著者, 2026-05-14)
- **結論**: 🟡 **理論的整合性のみ確認、新規実装は不要**（evolve-anything の evolve パイプラインがほぼ同等の機構を既に保持）
- **再評価トリガー**: 論文のコード公開後に「経験→新スキル自動生成」の具体実装が evolve-anything の手法と本質的に異なる場合
- **実体**: AIエージェントが環境相互作用を通じて自律的にスキルを生成・洗練するフレームワーク。遺伝的プログラミング + LLM 推論の融合

## evolve-anything 側の現行実装と照合

| 論文の概念 | evolve-anything 側の対応 | 状態 |
|-----------|---------------------|------|
| 経験ログからのスキル自動生成 | `discover/` (sessions.jsonl → 行動パターン → skill triage) | ✅ |
| 遺伝的バリエーション生成 | `bench/mutation_injector.py` (rule_delete / trigger_invert / prompt_truncate) | ✅ |
| Fitness 評価 + 選択 | `scripts/rl/fitness/environment.py` (coherence + telemetry + constitutional + skill_quality 重み統合) | ✅ |
| 自己進化サイクル | `evolve` skill (Observe → Diagnose → Compile → Housekeeping) | ✅ |
| 能力段階の追跡 | `growth_level.py` (env_score → Lv.1-10 + 称号) | ✅ |
| 経験ベースの能力拡張 | `skill_evolve.py` (Pre-flight / pitfalls.md ピンポイント組み込み) | ✅ |

## 採否理由

論文が主張する「経験→新スキル自動生成 + 遺伝的選択」のコアループは、evolve-anything が既に 3 ステージパイプライン (`evolve`) として実装済み。`mutation_injector.py` の 3 種類のミューテーションと `bench/run_benchmark.py` の sentinel 評価が遺伝的選択に相当する。

論文側で **新しい知見はあるが、実装層では取り入れる対象が見当たらない**:
- 我々の fitness は 4 軸統合 (coherence/telemetry/constitutional/skill_quality)、論文の「複雑意思決定問題で人間レベル」も同種の多軸評価が想定される
- discover → triage → evolve の流れは既に「経験から新スキル候補生成」を実装している

## 借りる価値のあるアイデア（保留枠）

論文公開後に確認すべき点:
- **fitness 形式**: 我々の `environment` fitness 動的重み (0.25 + 0.45 + 0.30) との比較で重み構成の理論根拠が得られるか
- **selection pressure**: 我々の `prune` skill は閾値ベースだが、論文側に確率的選択戦略があれば参考になる
- **新スキル生成のトリガー**: discover の Jaccard 閾値 (現状 0.6) との比較

## 推奨アクション

| 概念 | 推奨度 | アクション | 再評価条件 |
|------|--------|------------|------------|
| 全体的な evolve 機構 | 不要 | 既実装、追加実装なし | 論文のコード公開後、fitness の重み根拠 / selection 戦略を比較 |

## 関連

- 先行研究: [`skillos-tech-eval.md`](skillos-tech-eval.md) (SkillOS 論文評価)
- 現行コード: `scripts/lib/skill_evolve.py`, `scripts/bench/mutation_injector.py`, `scripts/rl/fitness/environment.py`
