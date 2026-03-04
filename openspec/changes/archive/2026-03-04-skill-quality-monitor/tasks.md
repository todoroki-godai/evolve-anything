## 1. 品質ベースライン記録（quality-baseline）

- [x] 1.1 scripts/quality_monitor.py を作成し、高頻度 global/plugin スキル判定ロジックを実装する（usage.jsonl から直近 HIGH_FREQ_DAYS 日で HIGH_FREQ_THRESHOLD 回以上使用のスキルを classify_artifact_origin() で "global" or "plugin" と判定されるものに限定して抽出）
- [x] 1.2 quality_monitor.py の先頭に定数を定義する（RESCORE_USAGE_THRESHOLD=50, RESCORE_DAYS_THRESHOLD=7, DEGRADATION_THRESHOLD=0.10, HIGH_FREQ_THRESHOLD=10, HIGH_FREQ_DAYS=30, MAX_RECORDS_PER_SKILL=100）
- [x] 1.3 global/plugin スキルの SKILL.md パス解決ロジックを実装する（~/.claude/skills/{name}/SKILL.md）
- [x] 1.4 品質計測関数を実装する（optimize.py の _llm_evaluate() と同一プロンプトで `claude -p` を直接呼び出し、結果を quality-baselines.jsonl に追記）
- [x] 1.5 quality-baselines.jsonl のレコードスキーマを実装する（skill_name, score, criteria, timestamp, usage_count_at_measure, skill_path）
- [x] 1.6 スキルあたりのレコード数上限（MAX_RECORDS_PER_SKILL=100）を超えた場合に古いレコードを削除する処理を実装する
- [x] 1.7 `claude -p` のタイムアウト・エラー時に計測をスキップし、既存レコードを不変に保つエラーハンドリングを実装する
- [x] 1.8 quality_monitor.py の CLI インターフェースを実装する（--dry-run オプション付き）

## 2. 劣化検知エンジン（degradation-detector）

- [x] 2.1 再スコアリングトリガー判定を実装する（前回計測からの使用回数 >= RESCORE_USAGE_THRESHOLD OR 経過日数 >= RESCORE_DAYS_THRESHOLD）
- [x] 2.2 ベースラインスコア（最高スコア）と直近3回の移動平均の算出ロジックを実装する
- [x] 2.3 劣化判定ロジックを実装する（ベースラインからの低下率 >= DEGRADATION_THRESHOLD）
- [x] 2.4 劣化検知時の推奨通知メッセージ生成を実装する（スキル名・現在スコア・ベースライン・低下率・推奨コマンド）
- [x] 2.5 計測履歴が3回未満の場合のフォールバック処理を実装する

## 3. audit レポート統合（audit-report）

- [x] 3.1 audit.py に quality-baselines.jsonl の読み込み関数を追加する
- [x] 3.2 スパークライン生成関数を実装する（Unicode ブロック文字でスコア推移を視覚化、例: `commit  ▁▃▅▇▅▃ 0.74 DEGRADED → /optimize commit`）
- [x] 3.3 generate_report() に "## Skill Quality Trends" セクションを追加する
- [x] 3.4 劣化スキルに "DEGRADED" 警告マーカーと /optimize 推奨コマンドを表示する
- [x] 3.5 再スコアリングが必要なスキルに "RESCORE NEEDED" マーカーを表示する
- [x] 3.6 quality-baselines.jsonl が存在しない場合のフォールバック処理を追加する
- [x] 3.7 audit.py に `--skip-rescore` オプションを追加し、品質計測をスキップして既存スコアのみでレポート生成できるようにする
- [x] 3.8 audit 実行時に quality_monitor.py の再スコアリング判定を呼び出し、必要なスキルの品質計測を実行してからレポートを生成する統合フローを実装する

## 4. SKILL.md 更新

- [x] 4.1 skills/audit/SKILL.md に品質モニタリングの実行手順を追加する（quality_monitor.py の実行 + audit レポートでの確認）

## 5. テスト

- [x] 5.1 scripts/quality_monitor.py のユニットテストを作成する（高頻度スキル判定、ベースライン記録、トリガー判定、劣化判定、LLMエラー時スキップ、レコード上限）
- [x] 5.2 audit.py の品質推移セクション生成のユニットテストを作成する（スパークライン、警告マーカー、フォールバック、--skip-rescore）
- [x] 5.3 全テストスイートを実行して既存テストが壊れていないことを確認する（python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v）
