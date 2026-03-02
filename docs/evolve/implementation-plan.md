# 段階的実装計画

## 概要

feedback-system change の機能を evolve ビジョンに統合した上で、段階的に実装する。

```
Step 1: 観測基盤     ← hooks + telemetry + feedback
Step 2: 健康診断     ← audit + report
Step 3: 淘汰         ← prune
Step 4: 発見         ← discover
Step 5: 統合         ← /evolve ワンコマンド
Step 6: 最適化拡張   ← ルール対応、戦略学習
```

---

## Step 1: 観測基盤（Observe）

環境観測 + 最適化テレメトリ + フィードバックコマンド。

### 1a. 環境観測 hooks

- [ ] `hooks/observe.py` — PostToolUse async hook（使用スキル・ファイルパス・エラー記録）
- [ ] `hooks/observe.py` — global スキル使用時にプロジェクトパスも記録（Usage Registry）
- [ ] `hooks/session_summary.py` — Stop async hook（セッション要約記録）
- [ ] `hooks/save_state.py` — PreCompact async hook（進化状態のチェックポイント）
- [ ] `hooks/restore_state.py` — SessionStart compact hook（状態復元）
- [ ] `hooks.json` にhook定義を追加

### 1b. 最適化テレメトリ（旧 feedback-system の execution-telemetry）

- [ ] `Individual` クラスに `strategy` フィールド追加 + `to_dict()` 更新
- [ ] `mutate()` → `strategy = "mutation"`、`crossover()` → `strategy = "crossover"`
- [ ] `next_generation()` エリート選出時 → `strategy = "elite"`
- [ ] `Individual` クラスに `cot_reasons` フィールド追加
- [ ] `_llm_evaluate()` から reason テキストを `cot_reasons` に保存
- [ ] `history.jsonl` に `rejection_reason` フィールド追加
- [ ] 人間却下時のオプション理由入力

### 1c. フィードバックコマンド（旧 feedback-system の feedback-command）

- [ ] `skills/feedback/SKILL.md` — `/rl-anything:feedback` スキル
- [ ] gh 認証チェックフロー
- [ ] 対話フロー（カテゴリ → ドメイン → スコア → 自由記述）
- [ ] プライバシー保護ルール
- [ ] Issue 本文生成 → プレビュー → 送信
- [ ] 送信失敗時のローカル保存フォールバック

### 1d. GitHub Issue テンプレート（旧 feedback-system の issue-template）

- [ ] `.github/ISSUE_TEMPLATE/feedback.yml`
- [ ] `.github/ISSUE_TEMPLATE/config.yml`（blank_issues_enabled: false）

### テスト

- [ ] telemetry フィールドのテスト（strategy, cot_reasons, rejection_reason）
- [ ] hooks のスモークテスト

---

## Step 2: 健康診断（Audit + Report）

- [ ] 全 skills / rules / memory の棚卸しスクリプト
- [ ] 行数チェック（構造的制約のバリデーション）
- [ ] usage.jsonl からの使用状況集計
- [ ] `scripts/aggregate-runs.py`（クロスラン集計）
- [ ] 1画面レポート出力（Report フェーズ）
- [ ] `/rl-anything:audit` スキル

---

## Step 3: 淘汰（Prune）

- [ ] dead glob 検出（rules の paths 対象がマッチするか検査）
- [ ] zero invocation 検出（usage.jsonl ベース、30日ルール）
- [ ] 重複検出（意味的類似度 by LLM）
- [ ] アーカイブ提案 + 人間承認フロー
- [ ] 復元コマンド
- [ ] `/rl-anything:prune` スキル

---

## Step 4: 発見（Discover）

- [ ] 繰り返し行動パターンの検出（usage + sessions）
- [ ] 繰り返しエラーパターンの検出（errors）
- [ ] 繰り返し却下理由の検出（rejection_reason）
- [ ] pitfalls 蓄積からのルール候補生成
- [ ] claude-reflect 出力の取り込み（オプション）
- [ ] スキル/ルール候補の生成（構造的制約バリデーション付き）
- [ ] `/rl-anything:discover` スキル

---

## Step 5: 統合（Evolve）

- [ ] Step 1-4 + 既存 Optimize を統合するオーケストレーター
- [ ] `/rl-anything:evolve` ワンコマンドで全ライフサイクル実行
- [ ] 観測データ量による自動スキップ判定（3セッション / 10観測未満）
- [ ] `--dry-run` モード（レポートのみ）

---

## Step 6: 最適化の拡張

- [ ] rules/*.md も optimize 対象に（ルール用 fitness 関数）
- [ ] 戦略学習: telemetry の戦略別有効性から配分を自動調整
- [ ] CoT reason 活用: 過去の reason からバリエーション生成ヒントを抽出
- [ ] GEPA / Bandit ベース評価の検討

---

## 既存コンポーネントとの関係

```
既存（維持）:
  genetic-prompt-optimizer  → Optimize の中核エンジン
  rl-loop-orchestrator      → 深い最適化の自律ループ
  rl-scorer                 → 評価エージェント
  generate-fitness          → 評価関数の自動生成

新規:
  hooks/                    → Observe（環境観測）
  feedback skill            → Observe（ユーザーFB）
  aggregate-runs.py         → Report（クロスラン集計）
  audit skill               → Report（健康診断）
  prune skill               → Prune（淘汰）
  discover skill            → Discover（発見）
  evolve skill              → 全フェーズ統合
```

## Step 7: 評価関数の自己成長

- [ ] history.jsonl に `human_accepted` boolean を記録
- [ ] score-acceptance 相関の計算（直近20回）
- [ ] 相関低下時（< 0.50）に再キャリブレーション警告
- [ ] rejection_reason の頻度分析 → 欠落している評価軸の提案
- [ ] CoT reason のパターン分析 → 評価軸の重み調整提案
- [ ] adversarial probe: ゲーミング候補を生成して fitness の脆弱性検出
- [ ] `/rl-anything:evolve-fitness` スキル
- [ ] 全変更は人間承認が必要

詳細: [fitness-evolution.md](./fitness-evolution.md)

---

## Step 8: 肥大化制御の自動化

- [ ] 生成/更新パイプラインにサイズバリデーション組み込み
- [ ] evolve 実行時の bloat check（CLAUDE.md, MEMORY.md, rules 総数, skills 総数）
- [ ] 分割提案の自動生成（MEMORY.md → トピック別ファイル）
- [ ] Global ↔ Project 重複検出
- [ ] path-scoped rules の自動推奨（universal rule が特定パスでしか使われていない場合）
- [ ] Usage Registry: global スキル/ルールの使用状況をプロジェクト横断で追跡
- [ ] Scope Advisor: Usage Registry ベースのスコープ最適化提案（global ↔ project 移動）
- [ ] Plugin Bundling 提案: 常に一緒に使われるスキル群の plugin 化推奨

詳細: [bloat-control.md](./bloat-control.md)

---

## 旧 feedback-system change との関係

feedback-system change の全機能は本ビジョンの Step 1 に統合された:

| feedback-system のスコープ | evolve ビジョンでの位置 |
|---------------------------|----------------------|
| execution-telemetry spec | Step 1b: 最適化テレメトリ |
| feedback-command spec | Step 1c: フィードバックコマンド |
| issue-template spec | Step 1d: GitHub Issue テンプレート |
| aggregate-runs.py | Step 2: クロスラン集計 |

feedback-system change は archived（evolve ビジョンに吸収）とする。
