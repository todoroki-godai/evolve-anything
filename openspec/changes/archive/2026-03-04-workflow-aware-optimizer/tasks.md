## 1. workflow_analysis.py（ワークフロー統計スクリプト）

- [x] 1.1 `scripts/rl/workflow_analysis.py` を作成。workflows.jsonl を読み取りスキル別統計 JSON を `~/.claude/rl-anything/workflow_stats.json` に出力
- [x] 1.1b workflows.jsonl が存在しない/空の場合は `{}` を出力し stderr に警告
- [x] 1.2 抽象パターン圧縮（連続同一エージェントを1つに集約）の実装
- [x] 1.3 team-driven は `team:<team_name>`、agent-burst は `(agent-burst)` をキーとして統計生成
- [x] 1.4 `--min-workflows N` オプションでフィルタリング（デフォルト 3）
- [x] 1.5 `--hints` オプションで optimizer 向け mutation ヒントテキスト生成
- [x] 1.6 `--for-fitness` オプションで generate-fitness 統合用出力
- [x] 1.7 テスト: 基本統計・抽象パターン圧縮・min-workflows フィルタ・ヒント生成・fitness 出力

## 2. GeneticOptimizer への mutation ヒント注入

- [x] 2.1 optimize.py の `mutate()` にワークフロー統計 JSON 読み込みロジック追加
- [x] 2.2 mutation プロンプトにワークフロー分析ヒントテキストを注入する処理を追加
- [x] 2.3 ワークフロー統計が存在しない場合は従来動作にフォールバック
- [x] 2.4 テスト: ヒント注入あり/なしの mutation プロンプト生成・フォールバック動作

## 3. rl-scorer ワークフロー品質軸

- [x] 3.1 agents/rl-scorer.md にワークフロー効率性の補助評価軸を追加
- [x] 3.2 ワークフロー統計 JSON 参照の指示を追加（存在時のみ使用、なければスキップ）

## 4. generate-fitness ワークフロー統計 + `--ask` 対応

- [x] 4.1 analyze_project.py にワークフロー統計 JSON（`~/.claude/rl-anything/workflow_stats.json`）のマージロジック追加
- [x] 4.2 analyze_project.py に `.claude/fitness-criteria.md` の読み込みロジック追加（存在時のみ）
- [x] 4.3 fitness-template.py にワークフロー統計参照のコメント・スケルトン追加
- [x] 4.4 SKILL.md に `--ask` オプションを追加。AskUserQuestion でユーザーに品質基準を質問
- [x] 4.5 `--ask` の回答を `.claude/fitness-criteria.md` に保存するロジック実装
- [x] 4.6 既存 fitness-criteria.md がある場合の更新確認フロー実装
- [x] 4.7 テスト: ワークフロー統計あり/なし・fitness-criteria.md あり/なしの analyze_project.py 出力

## 5. 統合

- [x] 5.1 CHANGELOG.md に v0.9.0 エントリ追加
- [x] 5.2 全テスト通過確認
