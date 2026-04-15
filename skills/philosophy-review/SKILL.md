---
name: philosophy-review
effort: medium
description: |
  Claude Code セッション履歴を Judge LLM で評価し、philosophy カテゴリ原則の違反例を抽出する。
  抽出した違反は corrections.jsonl に注入され、reflect ループで rule/memory 化判断される。
  Trigger: philosophy-review, 哲学レビュー, セッション哲学評価, 会話履歴レビュー
---

# /rl-anything:philosophy-review — 会話履歴ベースの哲学原則レビュー

`~/.claude/projects/<slug>/*.jsonl` の Claude Code native セッション履歴を対象に、
`principles.json` の `category: "philosophy"` 原則違反を Judge LLM で抽出する。

constitutional fitness は静的コンテンツ評価のため、会話・行動レベルの原則
（例: Karpathy 4原則）を測定できない。本スキルは月1回の手動 trigger で
直近セッションをサンプリングし、サイレントな違反を可視化する。

## Usage

```
/rl-anything:philosophy-review                       # 直近30セッションを評価
/rl-anything:philosophy-review --limit 10            # 評価対象数を指定
/rl-anything:philosophy-review --dry-run             # 評価のみ（corrections.jsonl 注入なし）
/rl-anything:philosophy-review --max-tokens 50000    # 1セッションあたり token cap
```

## 実行手順

### Step 1: philosophy_review.py を実行

```bash
rl-usage-log "philosophy-review"
python3 ~/.claude/skills/philosophy-review/scripts/philosophy_review.py [オプション]
```

スクリプトは以下を順に行う:
1. `principles.json` から `category: "philosophy"` の原則を抽出
2. `~/.claude/projects/<slug>/*.jsonl` から直近 N セッションを選択
3. 各セッションを token cap で truncate（長大セッション対策）
4. Judge LLM (claude haiku) で違反例を抽出
5. 違反例を corrections.jsonl に append（`source: "philosophy-review"`, `confidence: 0.85`）

### Step 2: 出力レポートをユーザーに提示

JSON 出力例:
```json
{
  "status": "ok",
  "sessions_evaluated": 30,
  "violations_found": 4,
  "violations_injected": 4,
  "details": [
    {
      "session_id": "abc-123",
      "principle_id": "think-before-coding",
      "evidence": "ユーザー要件が曖昧だったが、解釈確認なしに実装着手した",
      "confidence": 0.9
    }
  ]
}
```

### Step 3: 次のステップを案内

違反が検出された場合: `/rl-anything:reflect` を案内（corrections を rule/memory に反映）。
0件の場合: 「今期は哲学原則違反なし」と報告。

## エッジケース

- セッションログがない PJ → status: "no-sessions" を返して終了
- 全セッションが token cap 超過 → 上位 N 行 + 末尾 N 行サンプリング
- Judge LLM 失敗 → リトライ1回、失敗時はそのセッションをスキップして続行
- corrections.jsonl 書き込み失敗 → エラー報告のみ、評価結果は stdout に出力

## 関連
- `reflect`: 注入された corrections を CLAUDE.md/rules/memory に反映
- `principles.json`: 評価対象の哲学原則を定義（`category: "philosophy"`, `user_defined: true`）
