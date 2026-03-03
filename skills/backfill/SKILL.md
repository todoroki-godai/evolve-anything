---
name: backfill
description: |
  Backfill session transcripts to extract Skill/Agent tool calls and workflow structures,
  then output analysis reports. Writes to usage.jsonl and workflows.jsonl.
  Trigger: backfill, バックフィル, session history, セッション履歴, 分析
disable-model-invocation: true
---

# /rl-anything:backfill — セッション履歴のバックフィル＋分析

既存の Claude Code セッショントランスクリプトから Skill/Agent ツール呼び出しとワークフロー構造を抽出し、
usage.jsonl / workflows.jsonl にバックフィルした上で分析レポートを出力する。

## Usage

```
/rl-anything:backfill                        # バックフィル＋分析
/rl-anything:backfill --force                # 既存バックフィルを削除して再実行＋分析
```

## 実行手順

### Step 1: バックフィル実行

```bash
python3 <PLUGIN_DIR>/skills/backfill/scripts/backfill.py --project-dir "$(pwd)"
```

結果の JSON サマリを表示する（MUST）。
出力される JSONL ファイル:
- `usage.jsonl` — Skill/Agent ツール呼び出しレコード（parent_skill/workflow_id 付き）
- `workflows.jsonl` — ワークフロー単位のシーケンスレコード（Skill → Agent の構造）
- `sessions.jsonl` — セッション単位のメタデータ（全ツール名+順序、ツール種別カウント、セッション長、エラー数、ユーザー意図分類）

### Step 2: Intent 再分類（LLM Hybrid）

キーワード分類で "other" に残ったプロンプトを Claude 自身が再分類する。

1. "other" プロンプトを抽出する:

```bash
python3 <PLUGIN_DIR>/skills/backfill/scripts/reclassify.py extract --project "$(basename $(pwd))"
```

2. 出力された JSON を確認し、各プロンプトを以下のカテゴリに分類する:
   `spec-review`, `code-review`, `git-ops`, `deploy`, `debug`, `test`, `code-exploration`, `research`, `implementation`, `config`, `conversation`, `other`

3. 分類結果を JSON ファイルに書き出す（形式: `{"reclassified": [{"session_id": "...", "intent_index": N, "category": "..."}]}`）

4. 結果を書き戻す:

```bash
python3 <PLUGIN_DIR>/skills/backfill/scripts/reclassify.py apply --input <reclassified.json>
```

結果サマリ（updated_sessions, updated_intents）を表示する（MUST）。

### Step 3: 分析レポート出力

```bash
python3 <PLUGIN_DIR>/skills/backfill/scripts/analyze.py --project "$(basename $(pwd))"
```

マークダウン形式の分析レポートをユーザーに表示する（MUST）。
レポート内容: ワークフロー一貫性分析、ステップバリエーション、介入分析（workflow 内 vs ad-hoc）、Discover/Prune 比較データ。

### Step 4: --force による再実行（オプション）

ユーザーが `--force` を指定した場合のみ、Step 1 のコマンドに `--force` を追加する。
既存のバックフィルレコード（usage.jsonl, workflows.jsonl の source:"backfill"）を削除して全セッションを再処理する。

## allowed-tools

Read, Bash, Glob, Grep

## Tags

backfill, observe, usage, history, analyze, workflow
