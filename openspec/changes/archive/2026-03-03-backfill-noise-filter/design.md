## Context

backfill + reclassify の4プロジェクト実データ検証結果：

| プロジェクト | Total intents | 再分類 | 残 other | conversation |
|------------|--------------|--------|---------|-------------|
| atlas-breeaders | 974 | 105 | 536 | 31 |
| docs-platform | 3,378 | 502 | 1,547 | 269 |
| figma-to-code | 377 | 149 | 12 | 114 |
| ooishi-kun | 1,824 | 416 | 619 | 279 |

3つの問題が判明：
1. ノイズ混入（ooishi-kun の conversation 279 件中、大半がシステムメッセージ）
2. subprocess `claude -p --model haiku` は Max 環境で不要
3. `reclassified_intents` 存在セッションの残 "other" が再分類不可

## Goals / Non-Goals

**Goals:**
- システムメッセージを `user_prompts` / `user_intents` からフィルタし、分類精度を向上させる
- `<command-name>` タグからスキル名を抽出し、ワークフロー分析に活用する
- reclassify の LLM 呼び出しを Claude Code ネイティブに移行し、subprocess 依存を除去する
- 既分類セッションの残 "other" を再分類可能にする

**Non-Goals:**
- リアルタイム hooks のフィルタ変更（hooks は tool_use ベースで動作しており、この問題は発生しない）
- "conversation" カテゴリの細分化（ノイズ除去後のデータ量を見て別途判断）
- API ユーザー向けの subprocess フォールバック（必要になった時点で追加）

## Decisions

### Decision 1: フィルタ対象の分類

| パターン | 処理 | 理由 |
|---------|------|------|
| `[Request interrupted` で始まる | 完全除外 | ユーザー意図なし。中断操作の記録に過ぎない |
| `<command-name>` タグを含む | スキル名抽出 → intent `skill-invocation` | コマンド名は有用な情報。`/commit` → git-ops ではなく、スキル呼び出しという事実を記録する |
| `<local-command-` タグを含む | 完全除外 | コマンド出力やシステム注意書き。ユーザー意図ではない |
| `<task-notification>` タグを含む | 完全除外 | エージェント通知。ユーザー意図ではない |

**代替案: `<command-name>` もすべて除外する**
→ 不採用。スラッシュコマンドはユーザーが意図的に実行した操作であり、ワークフロー分析に有用。

**代替案: `<command-name>` から intent を推定する（例: `/commit` → `git-ops`）**
→ 不採用。マッピングのメンテナンス負荷が高く、未知のコマンドを扱えない。`skill-invocation` で一律記録し、分析フェーズで掘り下げる方が柔軟。

### Decision 2: フィルタの実装箇所

`parse_transcript()` 内の human メッセージ処理で、`classify_prompt()` を呼ぶ前にフィルタ関数を適用する。フィルタロジックは `_classify_system_message(content: str)` として分離し、テスタビリティを確保する。

返値は 3 パターン:
- `None` — 除外（`user_prompts` / `user_intents` に記録しない）
- `("skill-invocation", extracted_name)` — コマンド名を抽出して記録
- `("passthrough", content)` — 通常のユーザープロンプトとして処理

### Decision 3: subprocess 廃止 → Claude Code ネイティブ LLM

**現行アーキテクチャ（廃止）:**
```
SKILL.md → Bash(reclassify.py auto) → subprocess(claude -p --model haiku) → parse JSON
```

**新アーキテクチャ:**
```
SKILL.md Step 2:
  1. Bash(reclassify.py extract --project ...) → JSON 出力
  2. Claude Code が JSON を読み、各プロンプトを分類（ネイティブ LLM）
  3. 分類結果を JSON ファイルに書き出し
  4. Bash(reclassify.py apply --input <result.json>)
```

**理由:**
- Claude Code Max ではセッション内 LLM にコスト追加なし
- subprocess のスポーン・タイムアウト管理・JSON パースが不要になり、コードが大幅にシンプル化
- バッチサイズ制約もなくなる（Claude Code のコンテキストウィンドウに収まる範囲で一括処理可能）
- モデルはセッション依存（Max: Opus/Sonnet、分類タスクにはどちらも十分）

**`auto` サブコマンドの削除:**
`reclassify.py` から `auto` サブコマンド、`_build_classify_prompt()`、`_call_claude_classify()`、`auto_reclassify()` を削除する。テストも同様。

### Decision 4: 再分類スキップ問題の修正

`extract_other_intents()` が `reclassified_intents` の存在するセッションをスキップする仕様を変更する。

**現行:** `if record.get("reclassified_intents"): continue`（セッション丸ごとスキップ）
**変更後:** `reclassified_intents` が存在する場合、その中の "other" を抽出対象とする

`--include-reclassified` フラグで制御する（デフォルトは互換のためスキップ維持）。SKILL.md Step 2 では常にこのフラグを付けて実行する。

### Decision 5: `skill-invocation` カテゴリの追加

`common.PROMPT_CATEGORIES` にはキーワードとして追加しない（タグ構造で決定的に判定できるため）。reclassify.py の `VALID_CATEGORIES` に追加し、Claude Code の分類結果としても受け入れ可能にする。

**追加方法:** `VALID_CATEGORIES` の生成式を以下のように変更する:
```python
# 現行
VALID_CATEGORIES = list(common.PROMPT_CATEGORIES.keys()) + ["other"]
# 変更後
VALID_CATEGORIES = list(common.PROMPT_CATEGORIES.keys()) + ["other", "skill-invocation"]
```

### Decision 6: SKILL.md Step 2 の分類プロンプト設計

subprocess 廃止により `_build_classify_prompt()` が削除されるため、分類プロンプトのテンプレートを SKILL.md の Step 2 に直接記載する。Claude Code がネイティブ LLM として分類を実行する際の指示として機能する。

**SKILL.md Step 2 に含める内容:**
1. `reclassify.py extract --include-reclassified` の実行指示
2. カテゴリ定義一覧（`VALID_CATEGORIES` の全カテゴリ + 各カテゴリの判断基準）
3. 出力形式の指定（`{"reclassified": [{"session_id": "...", "intent_index": N, "category": "..."}]}` 形式の JSON ファイル）
4. `reclassify.py apply --input <result.json>` の実行指示

**分類プロンプトのテンプレート（SKILL.md Step 2 に記載する内容）:**

```
有効なカテゴリ:
- spec-review: 仕様レビュー、要件確認
- code-review: コードレビュー、変更確認
- git-ops: git 操作（commit, push, merge 等）
- deploy: デプロイ、リリース
- debug: デバッグ、バグ修正、エラー調査
- test: テスト実行、検証
- code-exploration: コード探索、ファイル確認
- research: 調査、ベストプラクティス
- implementation: 実装、機能追加
- config: 設定、構成
- conversation: 会話的応答（挨拶、確認、指示）
- skill-invocation: スキル/コマンド呼び出し
- other: 上記に該当しない場合のみ
```

**理由:**
- `_build_classify_prompt()` のテンプレートが失われると、分類の一貫性が保てない
- SKILL.md に直接記載することで、Claude Code が参照する唯一の真実源（single source of truth）となる
- カテゴリ追加時は SKILL.md と `VALID_CATEGORIES` の両方を更新する必要がある（これは `_build_classify_prompt()` + `VALID_CATEGORIES` の二重管理と同等のメンテナンス負荷）

## Risks / Trade-offs

- **[Risk] フィルタパターンの網羅性** → Claude Code のバージョンアップで新しいシステムタグが追加される可能性がある。prefix マッチで実装し、未知のタグは通常プロンプトとして扱う（false negative は許容、false positive は避ける）
- **[Risk] 過去データとの整合性** → フィルタ適用前にバックフィルされたデータはノイズを含む。`--force` 再実行で解消可能
- **[Trade-off] Claude Code 依存の強化** → subprocess 廃止により、SKILL.md 外からの単独実行（`python3 reclassify.py auto`）ができなくなる。Claude Code plugin としての利用が前提であり、許容する
- **[Trade-off] `--include-reclassified` のデフォルト** → 後方互換のためデフォルト off だが、SKILL.md では常に on。将来デフォルトを on に変更する可能性あり
