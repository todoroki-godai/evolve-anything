---
name: handover
effort: low
description: |
  セッションの作業状態を引き継ぎ用にまとめる。次セッションの冒頭にそのまま貼れるコンパクト形式で出力。
  Trigger: handover, 引き継ぎ, 作業引き継ぎ, hand off, 引き渡し, セッション引き継ぎ, 次のセッションで続き
---

# /rl-anything:handover — セッション引き継ぎ

現在のセッションの作業状態を、次セッションの冒頭にそのまま貼れる形で出力する。

## Usage

```
/rl-anything:handover          # コンパクト形式（画面出力のみ・デフォルト）
/rl-anything:handover --file   # コンパクト形式 + ファイル保存
/rl-anything:handover --deep   # 詳細形式（判断理由・廃案・デプロイ状態を含む）
/rl-anything:handover --issue  # GitHub Issue として作成
```

## Step 1: Git 状態取得

```bash
rl-usage-log "handover"
git rev-parse --abbrev-ref HEAD 2>/dev/null
git rev-parse --short HEAD 2>/dev/null
basename "$(git rev-parse --show-toplevel 2>/dev/null)"
```

取得できなければ「不明」とする。

## Step 2: モード分岐

- `--deep` フラグあり → **詳細モード**（Step 3b へ）
- `--issue` フラグあり → **Issue モード**（Step 3c へ）
- `--file` フラグあり or それ以外 → **コンパクトモード**（Step 3a へ。`--file` あり時はファイルにも保存）

## Step 3a: コンパクトモード（デフォルト）

会話コンテキストと `git log --oneline -10` から以下の3分類を判定して出力する。

### 分類ルール

| セクション | 含めるもの |
|---|---|
| **今日完了済み** | 「完了」「マージ」「LGTM」「できた」「done」と明言されたタスク。コミット・PR マージの事実 |
| **次にやること** | 「次は」「TODO」「あとで」「やる予定」と言及されたタスク。Issue 番号・ファイルパスが分かれば添える |
| **観察中（着手不可）** | 「様子見」「〜まで待つ」「監視中」「測定中」と言及されたタスク。期日・条件が分かれば添える |

### 出力形式

次のブロックをそのまま画面に出力する（Markdown コードブロックで囲まない）:

```
前セッションの続き。{リポジトリ名} の {ブランチ名} ({コミットハッシュ})。

今日完了済み：
- {完了した作業}

次にやること：
- {タスク}（{前提・ファイルパス・Issue番号など一言}）

観察中（着手不可）：
- {タスク}：〜{期日・条件} まで
```

**補足ルール**:
- 各セクションに該当なしの場合は「（なし）」と書く
- 「次にやること」は優先順に並べる
- 箇条書きは最小限。1行で収まらない場合は要点のみに絞る
- git/SPEC.md/ADR に既に記録されている詳細は省略する

`--file` フラグありなら `.claude/handovers/YYYY-MM-DD_HHmm.md` に Write で保存し、パスをユーザーに伝える。

## Step 3b: 詳細モード（--deep）

Step 1 のデータと会話コンテキストをもとに、以下のセクションで Markdown ノートを生成する。

**目的**: git/checkpoint/auto-memory では復元できない「判断の理由」と「廃案」を残す。

```markdown
# Handover: {日付} {時刻}

## Decisions
{決定事項とその理由（箇条書き）。「なぜそうしたか」を必ず含める}

## Discarded Alternatives
{検討したが捨てた選択肢とその理由（箇条書き）。なければ「なし」}

## Deploy State
- dev: {deployed / not deployed / 不明} (commit {hash})
- prod: {deployed / not deployed / 不明} (commit {hash})

## Next Actions
{次にやるべきこと（優先順付き箇条書き）。Deploy State を考慮する}

## Context (auto)
branch: {branch}
commit: {hash}
```

生成後、`.claude/handovers/YYYY-MM-DD_HHmm.md` に Write で保存してパスをユーザーに伝える。

プロジェクトに SPEC.md が存在する場合は `/rl-anything:spec-keeper update` を提案する（自動実行しない）。

## Step 3c: Issue モード（--issue）

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/handover/scripts/handover.py" --issue --project-dir "$(pwd)"
```

返却 JSON 内 `body` テンプレートの `<!-- LLM: ... -->` を会話コンテキストから埋め、以下で Issue 作成:

```bash
gh issue create --title "{title}" --body "{完成した body}" --label "handover"
```

ラベル `handover` が存在しない場合はラベルなしで作成。作成された Issue URL をユーザーに伝える。

## allowed-tools

Read, Write, Bash, Glob, Grep
