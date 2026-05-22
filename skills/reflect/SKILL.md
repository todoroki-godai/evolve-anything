---
name: reflect
effort: medium
description: |
  corrections を対話的にレビューし CLAUDE.md/rules/auto-memory に反映する。
  ユーザー修正の蓄積を構造化し、適切なメモリ層にルーティングする。
  Trigger: reflect, 振り返り, corrections, 修正反映, 学習
---

# /rl-anything:reflect — corrections の対話的レビューと反映

corrections.jsonl に蓄積されたユーザー修正を分析し、
適切なメモリ層（CLAUDE.md / rules / auto-memory）にルーティングして反映する。

## Usage

```
/rl-anything:reflect                        # 対話レビュー
/rl-anything:reflect --dry-run              # 分析のみ（書き込みなし）
/rl-anything:reflect --view                 # pending 一覧表示
/rl-anything:reflect --skip-all             # 全 pending をスキップ
/rl-anything:reflect --apply-all            # 高信頼度を自動適用
/rl-anything:reflect --min-confidence 0.70  # 閾値変更
/rl-anything:reflect --skip-semantic        # セマンティック検証スキップ
```

## 実行手順

### Step 1: reflect.py を実行

```bash
rl-usage-log "reflect"
rl-reflect [オプション]
```

### Step 2: 出力 JSON を読み取る

出力は JSON 形式。`status` フィールドで分岐する。

### Step 3: status が "empty" なら終了

「未処理の修正はありません」と表示して終了する。

### Step 4: --dry-run の場合

分析結果を表示するが Edit ツールを使わず終了する（MUST NOT edit）。
各 correction のルーティング提案・重複状態・信頼度を一覧表示する。

### Step 4.5: tool_call_analysis と error_class_summary を pitfall 生成コンテキストとして活用

出力 JSON に `tool_call_analysis` と `error_class_summary` が含まれる場合、pitfall 生成プロンプトに以下を追加すること：

**操作パターン軸（preceding_tool_calls より）:**
- `tool_call_analysis.failure_patterns` に 2 件以上出現するシーケンスがある場合、「ツール A の後にツール B を試みるパターンで誤りが多い」形式の pitfall 候補として提示する
- `tool_call_analysis.failure_rate_by_tool` で失敗率 0.3 以上のツールがある場合、そのツールに関連した注意事項を pitfall に含める

**エラー文脈軸（error_class より）:**
- `error_class_summary.by_class` の値を参照し、同セッションで API エラーが多発していた場合（tech: 3 以上）は、その後の修正が API 制約に起因する可能性があることを pitfall コンテキストとして注記する
- `error_class_summary.by_type` で特定のエラータイプが頻出している場合、そのエラータイプに固有の behavioral パターンを pitfall 生成時に考慮する

**pitfall 生成プロンプトに追加する軸:**
```
# 操作パターン軸（preceding_tool_calls より）
- どのツール操作の連続が失敗を招いているか？
- エラー直前に何を試みていたか？（例: Bash 失敗後に Edit を試みるパターン）

# エラー文脈軸（error_class より）
- behavioral エラー（将来: "behavioral" クラス）は行動パターンの pitfall 候補
- tech エラーが多い場合、API 制約を意識せず操作を続けたことによる修正の可能性
```

### Step 5: --apply-all の場合

corrections の `apply: true` のものを確認なしで適用する:
- Edit ツールで `suggested_file` に学習内容を書き込む
- corrections.jsonl の該当レコードの `reflect_status` を "applied" に更新

`apply: false` の corrections（閾値未満）は Step 6 の対話レビューに進む。

### Step 6: 各 correction を対話レビュー

#### importance_score フィルタ（Mem-π）

reflect.py が各 correction に `importance_score` (0.0〜1.0) を付与する。

**デフォルト動作:** `importance_score < 0.2` の correction は自動スキップ候補として表示し、
ユーザーに確認してからスキップする（強制スキップではない）。

importance_score の計算式:

```
confidence × max(0, 1 - elapsed_days / decay_days)
```

- elapsed_days: correction 記録からの経過日数
- decay_days: correction レコードの decay_days フィールド（デフォルト 90 日）

**3層メモリ参照 (issue #189)**:
出力 JSON の各 correction を以下の順で確認し、表示を調整する:
1. `duplicate_found: true` → 「semantic 層 (MEMORY.md) に記録済み」と表示し、自動スキップを提案する
2. `duplicate_in: "episodic"` → 「episodic 層に記録済み（`episodic_context.days_ago`日前）: `episodic_context.content`」と表示し、スキップを提案する
   - 例: 「3日前に適用済み: 'git diff で変更確認'」
3. 上記いずれでもない → 新規修正として通常レビューに進む

各 correction について AskUserQuestion で以下の選択肢を提供する:
- **approve**: suggested_file に書き込み → reflect_status を "applied" に更新 → **episodic 昇格 (後述)**
- **edit**: ユーザーの編集内容で書き込み → reflect_status を "applied" に更新 → episodic 昇格
- **false-positive**: 偽陽性として報告 → `false_positives.jsonl` に SHA-256 ハッシュを追記し、reflect_status を "skipped" に更新
- **skip**: reflect_status を "skipped" に更新

3件目以降は追加の選択肢を提供する:
- **skip-remaining**: 残り全件を "skipped" に更新して終了

#### episodic 昇格 (approve/edit 後に必ず実行)

correction を approve または edit で適用した後、以下を実行して episodic 層に昇格する:

```bash
rl-reflect --promote-episodic \
  --session-id "<correction の session_id>" \
  --timestamp  "<correction の timestamp>"
```

出力 JSON の `{"status": "promoted", ...}` を確認してから次の correction に進む。
`session_id` または `timestamp` がない correction は昇格をスキップする（silent ok）。

#### 書き込み時のルール

- suggested_file が既存ファイルの場合: 末尾に追記（Edit ツール）
- suggested_file が存在しないファイルの場合: 新規作成（Write ツール）
- routing_hint が "global" の場合: global スコープのファイルにのみ書き込む
- routing_hint が "skip" の場合: 対話レビューで表示するが、自動適用はしない
- line_limit_warning がある場合: 警告メッセージを表示し、分離を提案する

### Step 7: promotion_candidates の表示

corrections レビュー完了後、promotion_candidates がある場合は別セクションとして表示する。
「auto-memory への昇格候補」として、各候補のメッセージ・出現回数・推奨トピックを一覧表示する。
昇格の実行はユーザー判断に委ねる（自動実行しない）。

### Step 7.5: memory_update_candidates の表示

promotion_candidates 表示の後、memory_update_candidates がある場合は「MEMORY 更新候補」セクションとして表示する。
各候補の correction_message、memory_file、memory_line を一覧表示する。
更新の実行はユーザー判断に委ねる（自動実行しない）。

### Step 7.6: memory 更新時の update_count guard

`post_tool_use_memory.py` hook が Edit/Write ツール使用後に自動的に `update_count` を **+1** する（Issue #151）。LLM が手動でインクリメントすると二重カウントになるため、手動インクリメントは不要。

これは Issue #97 / arXiv:2605.12978 の「LLM 自己更新メモリの劣化」リスク (詳細: `docs/research/faulty-updated-memories.md`) に対する guard。

更新前に `update_count` が **3 以上** の memory に対しては以下の warning を表示しユーザー判断を仰ぐ:

```
⚠ {file} は過去 {update_count} 回 LLM 経由で更新済みです (arXiv:2605.12978)。
   元 corrections.jsonl を再参照し、再要約による情報減損が発生していないか確認してください。
   このまま更新しますか？
```

`update_count >= 3` は audit の `memory_heavy_update` issue としても集計される (`scripts/lib/audit/issues.py`, `MEMORY_HEAVY_UPDATE_THRESHOLD`)。

#### update_count リセット手順

`update_count >= 3` の memory を根本から書き直したい場合（情報減損を疑う場合）は以下を実行する:

1. **元 corrections.jsonl を参照** — `~/.claude/rl-anything/corrections.jsonl` から該当 memory の元 corrections を `source_correction_ids` で特定し、生の修正内容を確認する
2. **archive** — 既存ファイルを `{name}.archived-{YYYY-MM-DD}.md` にリネームして保持する（削除しない）
3. **新規作成** — `update_count: 0` の frontmatter で同名ファイルを新規作成し、元 corrections を参照して情報減損なく書き直す
4. **audit で確認** — `/rl-anything:audit` を実行し `memory_heavy_update` issue が解消されたことを確認する

ユーザーが「そのまま更新（リセットなし）」と答えた場合は通常通り更新して `update_count` を `+1` する。

### Step 8: 完了サマリを表示

- 処理件数（applied / skipped / remaining）
- 重複検出数
- promotion 候補数
- MEMORY 更新候補数

## allowed-tools

Read, Bash, Edit, Write, AskUserQuestion, Glob, Grep

## Tags

reflect, corrections, memory, routing, learning
