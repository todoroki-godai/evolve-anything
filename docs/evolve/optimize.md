# Phase 3: Optimize（最適化）

既存スキル/ルールの品質を LLM 直接パッチで改善する。

## 既存機能（維持）

- `/optimize <target>` — corrections/context ベースの直接パッチ最適化
- `/rl-loop <target>` — ベースライン取得 → 直接パッチ → 評価 → 人間確認のループ
- `/generate-fitness` — プロジェクト固有の評価関数を自動生成

## 2つのモード

### error_guided モード

corrections.jsonl に対象スキルのフィードバックがある場合、エラー分類に基づいてパッチを生成。

```
corrections.jsonl → エラー分類 → LLM 1パスパッチ → regression gate → accept/reject
```

### llm_improve モード

corrections がない場合、usage 統計・audit issues・pitfalls をコンテキストに含めた汎用改善。

```
context 収集 → LLM 1パスパッチ → regression gate → accept/reject
```

## evolve での拡張

### 1. 対象の自動選定

evolve 実行時に全アーティファクトをスキャンし、スコアが閾値以下のものを自動で候補に。

```
Optimize candidates:
  skills/bot-create — score 0.58 (threshold 0.70)
  rules/deploy-check — score 0.45 (threshold 0.60)
```

### 2. ルール対応

skills だけでなく `rules/*.md` も最適化対象に。
ルール用の fitness 関数は「明確性」「抽象化レベル」「3行以内か」で評価。

### 3. corrections の直接活用

corrections.jsonl に記録されたフィードバックを LLM パッチプロンプトに直接含め、
同じ問題が再発しないスキルを 1パスで生成する。

## クロスラン集計（aggregate-runs）

複数の optimize / rl-loop ラン間で傾向を集計するスクリプト。

```bash
python3 skills/audit/scripts/aggregate_runs.py --dir <results_dir>
```

出力:
- pitfalls パターンの出現頻度ランキング
- 承認率（approved / total）
- モード別の改善傾向

Report フェーズと `/evolve` のレポートで使用される。
詳細は [report.md](./report.md) を参照。
