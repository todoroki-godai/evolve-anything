---
name: evolve-loop
effort: high
description: |
  自律進化ループオーケストレーター。3役分担（実装者・採点者・進化者）で
  スキル/ルールを自律的に改善するループを回す。
  トリガーワード: evolve-loop, 自律進化, 自律ループ, evolution loop
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion, Agent
---

# 自律進化ループ

ベースライン取得 → 直接パッチ → 評価 → 人間確認のサイクルを回し、スキル/ルールを自律的に改善する。

現在のエフォートレベル: **${CLAUDE_EFFORT}**

- `low`: `--loops` を 1 に固定。evolve-scorer の採点は tech サブエージェントのみで実行（haiku のみ）
- `medium` / `high`: 通常実行
- `max`: 指定ループ数に +1 して実行（例: `--loops 3` → 4 ループ）

## 実行手順

ユーザーが `/evolve-loop` を呼び出したら、以下の手順で実行する。
（設計文脈 vs naive 生成の比較較正実験を行いたい場合は、本手順でなく末尾の
「較正実験」節を参照）

### 1. 引数をパースする

ユーザーの入力から以下の引数を抽出する。指定がなければデフォルト値を使用。

| 引数 | 説明 | デフォルト |
|------|------|-----------|
| `TARGET` | 改善対象のスキルファイルパス（スキル名 or ファイルパス） | 必須 |
| `--loops N` | ループ回数 | 1 |
| `--auto` | 人間確認ステップをスキップ | false |
| `--dry-run` | 構造テスト（実際の変更は行わない） | false |
| `--output-dir DIR` | 出力ディレクトリ | `.evolve-loop/` |
| `--evolve` | 自己進化パターン組み込みを有効化（Step 5.5） | false |
| `--evolve-search` | BES 前向き進化探索（#256）を有効化。subgoal fitness で重み付けした crossover/mutate の子候補を既存 variants に合流させる | false |
| `--no-selection-reeval` | 採用前再評価（winner's curse 補正、#234）を無効化 | false（=再評価は既定で有効） |
| `--selection-reeval-n N` | 採用前再評価の回数 | 3 |

TARGET がスキル名（例: `my-skill`）の場合、`.claude/skills/{name}/SKILL.md` に解決する。
ファイルパスが直接指定された場合はそのまま使用する。

### 2. スクリプトを実行する

```bash
evolve-usage-log "evolve-loop-orchestrator"
evolve-loop \
  --target <TARGET> [OPTIONS]
```

### 3. 結果をユーザーに報告する

- ベースラインスコアと最終スコアの比較を表示する
- 最高スコアのバリエーションの差分を提示する
- `--auto` でない場合、ユーザーに承認/却下を確認する
- エラーが発生した場合はエラー内容と対処法を提示する

## 使用例

```
/evolve-loop my-skill                    # 1ループ実行（人間確認あり）
/evolve-loop my-skill --dry-run          # 構造テスト
/evolve-loop my-skill --loops 3          # 3ループ実行
/evolve-loop my-skill --auto             # 人間確認スキップ
/evolve-loop my-skill --evolve           # 最適化 + 自己進化パターン組み込み
/evolve-loop my-skill --evolve --dry-run # 判定結果のみ表示
```

## ループの流れ

```
[Step 1] ベースライン取得: 現在のスキルを evolve-scorer で採点
    ↓
[Step 2] 直接パッチ: genetic-prompt-optimizer で corrections/context ベースの LLM 1パスパッチを生成
    ↓
[Step 3] 評価: 各バリエーションを evolve-scorer で採点、ベースラインと比較
    ↓
[Step 3.6] 採用前再評価（winner's curse 補正、#234）: IMPROVED 候補のみ追加 N 回
           再評価し、平均値で改善が消えれば格下げ（--no-selection-reeval で無効化）
    ↓
[Step 4] 選択と人間確認: 最高スコアのバリエーションを提示 → 承認/却下
    ↓
[Step 5] 記録: 結果を出力ディレクトリに保存
    ↓
[Step 5.5] 自己進化（--evolve 時のみ）: 未対応スキルに自己進化パターン組み込みを提案
    ↓
(次ループへ)
```

## 採点エージェント

evolve-scorer エージェント（`agents/evolve-scorer.md`）が統合スコアを算出する。

スコア構成:
- **技術品質スコア** (40%): 明確性・完全性・一貫性・エッジケース・テスト可能性
- **ドメイン品質スコア** (40%): CLAUDE.md からドメインを推定し、評価軸を自動選択
- **構造スコア** (20%): スキルファイルの構造的品質

## 出力先

```
.evolve-loop/                         # デフォルト（--output-dir で変更可）
├── {run_id}/
│   ├── baseline.json      # ベースラインスコア
│   ├── variants/           # 各バリエーション
│   ├── scores.json         # 各バリエーションのスコア
│   └── result.json         # 最終結果
└── history.jsonl           # 全ループの履歴
```

## 注意事項

- 最初のループは必ず人間確認ステップを含める（`--auto` は使わない）
- スコアが下降した場合は自動的にバックアップから復元
- 1ループの API コスト目安: sonnet x 1回（直接パッチ）+ haiku x 1回（評価）
- 長時間ループを `run_in_background: true` で実行する場合、CC v2.1.98+ の `Monitor` tool で stdout stream を受信すると進捗をリアルタイムに追える（sleep ポーリング不要）

## 較正実験: 設計文脈 vs naive 生成比較（opt-in）

harness 自動進化の改善が「探索予算増加（単純サンプリング, test-time scaling）」由来か
「設計改善（corrections/context を使った誘導）」由来かを切り分ける opt-in 較正実験
（#234, arXiv 2607.12227）。毎ループ実行の evolve-loop（上記の実行手順）に統計的対照
実験を混ぜ込むのは筋が悪いため、別コマンド `evolve-loop-ablation` として切り出している。

designed（corrections/context 込みプロンプト）と naive（corrections/context 抜きの
素のプロンプト）でそれぞれ n 件生成し、3軸スコアで比較する。dry-run が既定
（llm-batch-guard 準拠）で、LLM 呼び出しは `--run` を付けたときのみ発生する。

| 引数 | 説明 | デフォルト |
|------|------|-----------|
| `--target PATH` | 対象ファイルパス | 必須 |
| `--n N` | designed/naive 各条件の生成件数 | 3 |
| `--run` | 実際に LLM を呼ぶ（未指定は dry-run） | false |
| `--force` | designed/naive プロンプトが比較不能（実質同一）でも強制実行する | false |
| `--json` | 構造化 JSON 出力 | false |

```bash
evolve-loop-ablation --target <PATH>              # dry-run: 比較可能性チェック + コスト見積もりのみ
evolve-loop-ablation --target <PATH> --run         # designed/naive 各 n 件を生成・採点して比較
evolve-loop-ablation --target <PATH> --run --force # 比較不能でも強制実行
```

注意:
- 比較不能（designed/naive プロンプトが実質同一で corrections/context シグナルが無い）
  な場合、`--force` 未指定なら LLM 呼び出し前に自動中断する
- 対象ファイルは一切書き換えない（read-only ツール。適用は別途 `/evolve-loop` を使う）
