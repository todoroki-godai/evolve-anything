## Architecture

### auto サブコマンドの処理フロー

```
reclassify.py auto --project <name>
  │
  ├─ 1. extract_other_intents() で "other" プロンプト取得
  │
  ├─ 2. バッチ分割（MAX_BATCH_SIZE=50 件ずつ）
  │
  ├─ 3. 各バッチに対して claude -p --model haiku 呼び出し
  │     - stdin: 分類プロンプト + JSON 形式のプロンプト一覧
  │     - stdout: JSON 形式の分類結果
  │
  ├─ 4. 結果をパースして apply_reclassification() で書き戻し
  │
  └─ 5. サマリ出力
```

### LLM 分類プロンプト設計

- システムプロンプトで有効カテゴリ一覧と判定基準を提示
- ユーザープロンプトとして番号付きリストを入力
- JSON 配列で `[{"index": N, "category": "..."}]` を返却させる

### backfill.py の user_prompts 記録

- `user_intents` と同じタイミングで `user_prompts` リストに原文を追加
- `MAX_PROMPT_LENGTH` (500) で切り詰め
- sessions.jsonl の既存レコード構造に `user_prompts` フィールドを追加

## Pattern Selection

### subprocess 経由の claude CLI 呼び出し

Claude Code plugin はランタイム内から Anthropic SDK を直接呼び出せない。LLM を利用するには `claude -p` コマンドを subprocess で呼び出すのが唯一の手段である。

### バッチサイズ 50 件の根拠

- haiku の入力コンテキストを効率的に使いつつ、1 回のレスポンスで確実に JSON 配列を返せるサイズ
- 50 件 × 数バッチで $0.01 未満のコストを想定
- 大きすぎるとレスポンスの JSON パースが不安定になるリスクがある

## Rejected Alternatives

### キーワード辞書の拡充のみ
- **概要**: `common.PROMPT_CATEGORIES` のキーワードを増やして "other" 率を下げる
- **不採用理由**: 自然言語の多様性に対してキーワードマッチには本質的な限界がある。キーワードを増やすほど誤分類（false positive）も増加し、メンテナンスコストが際限なく膨らむ

### Sonnet による高精度分類
- **概要**: haiku の代わりに Sonnet を使い分類精度を上げる
- **不採用理由**: 分類タスクに対して Sonnet はオーバースペック。コストが haiku の約 10 倍になり、日次運用のコスト見合いに合わない。haiku で十分な精度が得られる

### Python SDK 直接呼び出し
- **概要**: `anthropic` パッケージを import して API を直接呼び出す
- **不採用理由**: Claude Code plugin の実行環境では SDK の依存関係を保証できない。`claude` CLI は Claude Code 環境に常に存在するため、確実性が高い

## Commonality Analysis

### PROMPT_CATEGORIES と LLM 分類プロンプトの関係

`hooks/common.py` の `PROMPT_CATEGORIES` はキーワードベースのリアルタイム分類に使用するカテゴリ辞書であり、`_build_classify_prompt()` 内のカテゴリ説明は LLM に文脈を与えるための自然言語記述である。

両者は同じカテゴリ体系を共有するが、用途が異なる：
- **PROMPT_CATEGORIES**: キーワードリスト → 高速・LLM コストゼロ・精度に限界
- **LLM 分類プロンプト**: カテゴリの説明文 → LLM が意味を理解して判定

カテゴリの追加・削除時は両方を更新する必要がある。`VALID_CATEGORIES` を `common.PROMPT_CATEGORIES` から動的に生成することで、カテゴリ一覧の一元管理は実現済み。説明文の二重管理は許容する（LLM にはキーワードリストではなく、意味を伝える説明文が必要であるため）。

## Constraints

- `claude` CLI が PATH に存在すること（Claude Code 環境では保証される）
- haiku モデルのコスト: 50 件バッチ × 数回で $0.01 未満を想定
- `--dry-run` オプションで実際の LLM 呼び出しなしに動作確認可能
