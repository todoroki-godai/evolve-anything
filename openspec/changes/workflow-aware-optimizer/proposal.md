## Why

Phase B（ワークフロートレーシング）で 301 workflows を蓄積し分析した結果、以下が判明した:

- **抽象パターン一貫性は中程度**（圧縮後 ~45%）: エージェント種別の順序にはパターンがあるが、繰り返し回数はタスク依存
- **主な変動はエージェント呼び出し回数**であり、スキル構造の問題ではない
- **エージェントシーケンスの mutation はフィードバックループが閉じない**: ワークフロー構造は LLM が実行時に決定するため、genome を変異させても SKILL.md に反映できない

この分析結果は `docs/evolve/workflow-tracing.md` Phase C の判断基準「一貫性が高ければ構造化、ばらつきが大きければ自然言語」に照らすと、**自然言語ベースで既存 GeneticOptimizer を拡張する** のが最適解。独立したパイプライン（genome/fitness/mutator）ではなく、既存の最適化エンジンにワークフロー知見を注入する。

## What Changes

- **GeneticOptimizer の mutation プロンプトにワークフロー分析ヒントを注入**: 「このスキルのワークフローは Explore→Plan パターンが 50% だが一貫性が低い。エージェントの使い分けを明確にする指示を追加すべき」のようなヒントを mutation 時に渡す
- **rl-scorer にワークフロー品質軸を追加**: 抽象パターン一貫性、平均ステップ数、所要時間の分散をドメイン品質の補助シグナルとして使用
- **ワークフロー分析スクリプトの追加**: workflows.jsonl からスキル別のワークフロー統計を算出し、optimizer / scorer が参照できる JSON を出力
- **generate-fitness のワークフロー統計対応**: プロジェクト固有 fitness 関数生成時に、ワークフロー統計を入力として使えるようにする。atlas-breeders のような「ゲーマーのワクワク評価」等のドメイン固有指標と、ワークフロー効率性を組み合わせた fitness 関数を生成可能にする
- **generate-fitness の `--ask` オプション**: ユーザーに品質基準を対話的に質問し、`.claude/fitness-criteria.md` に保存。以降の fitness 関数生成時に自動参照される。これにより CLAUDE.md や rules から推定できないドメイン固有の品質基準をユーザーが明示的に指定できる

## Capabilities

### New Capabilities
- `workflow-analysis`: workflows.jsonl からスキル別ワークフロー統計（抽象パターン一貫性、ステップ数分布、所要時間）を算出し JSON 出力するスクリプト

### Modified Capabilities
- `fitness-generator`: analyze_project.py のワークフロー統計マージ + `--ask` オプションによる `.claude/fitness-criteria.md` 対応

## Impact

- `skills/genetic-prompt-optimizer/scripts/optimize.py` — mutation プロンプトへのヒント注入
- `agents/rl-scorer.md` — ワークフロー品質軸の追加
- `skills/generate-fitness/` — ワークフロー統計入力の対応
- 新規: `scripts/rl/workflow_analysis.py` — ワークフロー統計算出スクリプト
