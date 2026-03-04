## Context

rl-anything と claude-reflect は同じ UserPromptSubmit hook で修正パターンを検出しているが、データが分断されている:
- rl-anything: CJK 4パターン + 英語 2パターン → `corrections.jsonl` → discover/evolve で統計活用
- claude-reflect: 英語 12パターン (Explicit 1 + Positive 3 + Correction 8) + Guardrail 8パターン → `learnings-queue.json` → `/reflect` で CLAUDE.md に反映

claude-reflect の主要コンポーネント:
- `capture_learning.py`: パターン検出 + 信頼度計算 + キューイング
- `reflect_utils.py`: 6層メモリ階層ルーティング、セッション JSONL パース、パターン定数
- `semantic_detector.py`: `claude -p` による AI セマンティック検証、矛盾検出

### 前提: evolve-enrich-reorganize change

パイプラインが以下に拡張される前提で設計:
Observe → Fitness Check → Discover → **Enrich** → Optimize → **Reorganize** → Prune(+Merge) → Fitness Evolution → Report

本 change はこのパイプラインに **Reflect Step** を追加する。

## Goals / Non-Goals

**Goals:**
- CJK + 英語 + Guardrail の統一パターンセットで修正検出を一元化
- corrections.jsonl を拡張して learnings-queue.json の役割を吸収
- `/rl-anything:reflect` で corrections → CLAUDE.md/rules/skills 反映の対話的レビューを提供
- evolve パイプラインに Reflect Step を統合
- claude-reflect アンインストール後も全機能が動作すること

**Non-Goals:**
- claude-reflect の OSS としての改善（吸収後はアップストリーム追従しない）
- session_start_reminder / post_commit_reminder hook の移植（低価値、通知のみ）
- `--dedupe` / `--organize` の初回リリースへの含有（将来 audit に統合）
- `claude -p` 以外のセマンティック検証手法（現状の subprocess バッチ呼び出しを維持）
- `source: "history-scan"` の初回リリースへの含有（将来拡張として `source` は `"hook" | "backfill"` の2値に限定）
- `/rl-anything:reflect-skills` の独立スキル化（セッションテキスト分析は `/discover --session-scan` に統合）
- `/reflect --include-tool-errors` の初回リリースへの含有（ツールエラー抽出は将来 evolve 新フェーズまたは別スキルとして独立化）

## Decisions

### D1: パターン統合 — common.py に全パターンを集約

**選択**: `hooks/common.py` の `CORRECTION_PATTERNS` に claude-reflect の英語 12 (Explicit 1 + Positive 3 + Correction 8) + Guardrail 8 パターンをマージ。

**理由**:
- 現在の `CORRECTION_PATTERNS` は CJK 4 + 英語 2 の 6 パターンのみ
- claude-reflect の `detect_patterns()` は信頼度計算（長さ調整、強弱フラグ）が成熟している
- 統合により correction_detect.py が全パターンを1回で処理

**`detect_correction()` 戻り値型**: タプル `(correction_type: str, confidence: float)` を維持。内部実装は辞書引きに変更するが、戻り値のアンパックインターフェースは変更しない。これにより `backfill.py` の `correction_type, _ = result` や `test_correction_detect.py` の `result[0]` が壊れない。

**パターン分類の統合方式**:
```python
CORRECTION_PATTERNS = {
    # 既存 CJK
    "iya": {"pattern": r"^いや[、,.\s]", "confidence": 0.85, "type": "correction"},
    "chigau": {"pattern": r"^違う[、,.\s]", "confidence": 0.85, "type": "correction"},
    ...
    # claude-reflect 由来: explicit
    "remember": {"pattern": r"(?i)^remember:", "confidence": 0.90, "type": "explicit", "decay_days": 120},
    # claude-reflect 由来: guardrail
    "dont-unless-asked": {"pattern": r"(?i)don't (add|include|create).*unless.*ask", "confidence": 0.90, "type": "guardrail", "decay_days": 120},
    ...
    # claude-reflect 由来: correction (strong)
    "no-use": {"pattern": r"(?i)^no,?\s*(use|try)", "confidence": 0.70, "type": "correction", "strong": True},
    ...
}

FALSE_POSITIVE_PATTERNS = [
    r"\?$",              # 既存: 疑問文
    r"(?i)^(can you|could you|would you)",  # claude-reflect 由来
    r"(?i)^(please|help me)",               # claude-reflect 由来
    ...
]
```

### D1.1: correction_detect.py の処理パイプライン順序

hook 受信から corrections.jsonl 書込みまでの処理順序を以下に定義する:

1. `should_include_message(message)` — XMLタグ・JSON・500文字超をフィルタ（"remember:" はバイパス）
2. `detect_correction(message)` — CORRECTION_PATTERNS を走査し `(correction_type, confidence)` タプルを返す
3. FALSE_POSITIVE_PATTERNS による除外チェック
4. `calculate_confidence(base_confidence, message)` — 長さ調整（短文ブースト/長文削減）
5. スキーマ構築 — 拡張フィールド（project_path, sentiment, decay_days 等）を付与して JSONL 書込み

### D2: corrections.jsonl スキーマ拡張

**選択**: 既存フィールドを維持しつつ、learnings-queue.json の役割を吸収するフィールドを追加。

```jsonl
{
  "timestamp": "...",
  "session_id": "...",
  "message": "原文",
  "correction_type": "iya",
  "matched_patterns": ["iya"],
  "last_skill": "commit",
  "confidence": 0.85,
  "sentiment": "correction",
  "decay_days": 90,
  "routing_hint": "global",
  "guardrail": false,
  "reflect_status": "pending",
  "extracted_learning": null,
  "project_path": "/Users/user/my-project",
  "source": "hook"
}
```

新規フィールド:
- `matched_patterns`: マッチした全パターンキーのリスト（例: `["no", "use-X-not-Y"]`）。`correction_type` は最初のマッチ（主パターン）、`matched_patterns` は信頼度計算に使用（3+パターン→0.85, 2パターン→0.75）
- `project_path`: 記録時の `CLAUDE_PROJECT_DIR`（プロジェクトフィルタリングに使用）
- `sentiment`: "correction" | "explicit" | "positive" | "guardrail"
- `decay_days`: corrections の有効期間（日数）。`/reflect` で古い corrections をスキップする判定に使用。パターンごとのデフォルト値（90 or 120）。**注意**: prune の `compute_decay_score()` の減衰定数とは無関係。また、`applied`/`skipped` レコードは `decay_days` 超過後に prune.py のクリーンアップ対象となる（後述 D12）
- `routing_hint`: "global" | "project" | "skill" | null（suggest_claude_file の結果）
- `guardrail`: bool
- `reflect_status`: "pending" | "applied" | "skipped"（/reflect 処理済みフラグ）
- `extracted_learning`: セマンティック検証後の簡潔な学習文（null = 未検証）
- `source`: "hook" | "backfill"（初回リリース範囲。`"history-scan"` は将来拡張）

### D3: /rl-anything:reflect スキルの設計 — 型A パターン

**選択**: reflect.py が corrections.jsonl から pending レコードを抽出・分析し JSON 出力 → SKILL.md の指示で Claude が対話レビュー。

**理由**: evolve-enrich-reorganize と同じ型A パターン（Python はデータ処理のみ、LLM は SKILL.md 経由）。

**CLI オプション**:
- `--dry-run`: 変更プレビューのみ。SKILL.md は Edit ツールを使わず分析結果を表示して終了
- `--view`: pending corrections の一覧表示のみ（confidence・タイプ・経過日数付き）
- `--skip-all`: 全 pending corrections を一括で `reflect_status: "skipped"` に更新
- `--apply-all [--min-confidence N]`: confidence >= N（デフォルト 0.85）の corrections を確認なしで一括 apply。閾値未満の corrections は対話レビューに進む（スキップではない）
- `--skip-semantic`: LLM セマンティック検証を無効化（デフォルト有効）
- `--model MODEL`: セマンティック検証モデル（デフォルト: sonnet）

**reflect.py の責務**:
1. corrections.jsonl から `reflect_status: "pending"` のレコードを抽出
2. セマンティック検証（デフォルト有効。全 pending corrections を1回の `claude -p` でバッチ検証）
3. プロジェクトフィルタリング（current project / global / other project）
4. 重複排除（既存 CLAUDE.md エントリとの照合）
5. ルーティング提案（8層メモリ階層）
6. JSON 出力

**reflect.py の出力 JSON スキーマ**:
```json
{
  "status": "has_pending",
  "corrections": [
    {
      "index": 0,
      "message": "いや、bun を使って",
      "correction_type": "iya",
      "confidence": 0.85,
      "routing_hint": "global",
      "suggested_file": "~/.claude/CLAUDE.md",
      "duplicate_found": false,
      "duplicate_in": null,
      "extracted_learning": "パッケージマネージャーには bun を使用する"
    }
  ],
  "summary": {
    "total": 3,
    "by_type": {"correction": 2, "guardrail": 1},
    "duplicates": 1
  }
}
```
pending が 0件の場合: `{"status": "empty", "message": "未処理の修正はありません"}`

**SKILL.md の責務**:
1. reflect.py の出力を読み取り
2. pending corrections を対話的レビュー（AskUserQuestion で approve/edit/skip/skip-remaining）
3. 承認されたものを Edit ツールで CLAUDE.md/rules/skills に書込
4. corrections.jsonl の `reflect_status` を "applied" | "skipped" に更新
5. promotion_candidates がある場合、pending corrections の後にまとめて表示

**対話レビューの UX**:
- 各 correction に対して approve/edit/skip の選択肢を提供
- 3件目以降は「残り全部 skip」（skip-remaining）の選択肢を追加し、レビュー疲労を軽減
- 昇格候補（promotion_candidates）は通常の corrections レビュー完了後に別セクションとして表示

### D4: 8層メモリ階層ルーティング — reflect_utils.py から移植

**選択**: `find_claude_files()`, `suggest_claude_file()`, `read_auto_memory()`, `suggest_auto_memory_topic()` を `scripts/reflect_utils.py` として移植。

**発見対象メモリ層**:

| 層 | ファイル | パス | 用途 |
|----|---------|------|------|
| global | CLAUDE.md | `~/.claude/CLAUDE.md` | 全プロジェクト共通の行動指示 |
| root | CLAUDE.md | `./CLAUDE.md` | プロジェクト固有 |
| local | CLAUDE.local.md | `./CLAUDE.local.md` | 個人用（gitignore 対象）。マシン固有のパスや個人設定 |
| subdirectory | CLAUDE.md | `./**/CLAUDE.md` | サブディレクトリ固有 |
| rule | *.md | `./.claude/rules/*.md` | モジュール化ルール（paths: スコープ対応） |
| user-rule | *.md | `~/.claude/rules/*.md` | グローバルルール |
| auto-memory | *.md | `~/.claude/projects/<project>/memory/*.md` | 低信頼度ステージング。トピック別自動分類 |
| skill | SKILL.md | `./.claude/commands/*/SKILL.md` | スキル実行時の修正 |

**ルーティング優先順位**:
1. guardrail タイプ → `.claude/rules/guardrails.md`
2. モデル名キーワード → model-preferences rule or `~/.claude/CLAUDE.md`
3. "always/never/prefer" → `~/.claude/CLAUDE.md`（global behavior）
4. rule の `paths:` frontmatter にマッチ → 対応 rule ファイル
5. サブディレクトリ名にマッチ → 対応 CLAUDE.md
6. 低信頼度 (confidence < 0.75) → auto-memory（トピック別ファイルに仮置き、後で昇格）
7. マシン固有・個人設定 → `./CLAUDE.local.md`（ユーザー選択時のみ）
8. マッチなし → Claude に判断委任

**Auto-memory トピック分類**:
```python
_AUTO_MEMORY_TOPICS = {
    "model-preferences": ["gpt-", "claude-", "gemini-", "o3", "o4", "model", "llm"],
    "tool-usage": ["mcp", "tool", "plugin", "api", "endpoint"],
    "coding-style": ["indent", "format", "style", "naming", "convention", "lint"],
    "environment": ["venv", "env", "docker", "port", "database", "redis", "postgres"],
    "workflow": ["commit", "deploy", "test", "build", "ci", "cd", "pipeline"],
    "debugging": ["debug", "error", "log", "trace", "breakpoint"],
}
```
キーワードスコアリングで最適トピックを選択。マッチなしは `"general"` に分類。

**Auto-memory 昇格ロジック**:

低信頼度 corrections は auto-memory に仮置きされる。以下の条件で `/reflect` 実行時に昇格候補として表示:

1. **再出現ブースト**: 同じ `correction_type` が 2回以上出現 → confidence を再計算し、閾値 (0.75) を超えたら昇格候補
2. **経年昇格**: auto-memory に 14日以上滞留し、矛盾する correction が記録されていない → 昇格候補
3. **手動昇格**: `/reflect` の対話レビューでユーザーが明示的に apply → 正式な CLAUDE.md/rules に書込み、auto-memory のエントリを削除

昇格候補は `/reflect` の出力 JSON に `"promotion_candidates"` キーで含まれる。

### D5: セマンティック検証 — scripts/lib/semantic_detector.py として移植

**選択**: `semantic_analyze()`, `validate_queue_items()`, `detect_contradictions()`, `validate_tool_errors()` を移植。

**理由**: regex 検出は偽陽性が多い（「いや」で始まるが修正ではないケース等）。LLM による二次検証で精度を上げる。

**使用箇所**:
- `/reflect` のフィルタリング（pending corrections のバッチ検証）
- `/reflect --dedupe` の矛盾検出（将来的に audit に統合）

**バッチ送信方式**: pending corrections を1件ずつ検証するのではなく、バッチで `claude -p` に送信する。これにより:
- レイテンシ: N回の LLM 呼び出し → 最小回数
- corrections 間の矛盾検出・重複検出も同じパスで実行可能

**バッチサイズ上限**: 1回の `claude -p` 呼び出しあたり最大 20件。20件超の場合は複数バッチに分割する。これによりトークン超過や精度低下を防止する。

**JSON パースエラーのフォールバック**: `claude -p` のレスポンスが不正な JSON（パース失敗、件数不一致等）の場合、タイムアウトと同様に regex 検出結果をフォールバックとして使用し、stderr に警告を出力する。

**デフォルト動作**: セマンティック検証はデフォルト有効。`--skip-semantic` で無効化可能。デフォルトモデルは `sonnet`。`--model haiku` で高速化可能。

### D6: evolve パイプラインへの統合 — Fitness Evolution の後

**選択**: Report の直前に Reflect Step を追加。

**理由**:
- Enrich/Optimize/Prune で既存スキルの改善が完了した後に、修正パターンの CLAUDE.md 反映を提案するのが自然
- corrections が溜まっている場合のみ動作（0件ならスキップ）

**パイプライン（完全版）**:
```
Observe → Fitness Check → Discover → Enrich → Optimize → Reorganize
→ Prune(+Merge) → Fitness Evolution → **Reflect** → Report
```

**evolve.py への追加**:
```python
# Phase N: Reflect（未処理 corrections の確認）
pending = count_pending_corrections()
result["phases"]["reflect"] = {"pending_count": pending}
```

SKILL.md の Reflect Step:
- `pending_count >= 5` または前回 `/reflect` 実行から 7日以上経過: 「N件の未処理修正があります。/reflect を実行しますか？」と AskUserQuestion
- `0 < pending_count < 5` かつ 7日以内: Report に「未処理修正 N件あり」と表示するのみ（提案しない）
- ユーザーが「実行」→ /rl-anything:reflect を実行
- ユーザーが「スキップ」→ Report へ

### D7: backfill との統合 — 統一パターンでの遡及抽出

**選択**: `backfill.py` の correction 抽出で統合後の全パターンを使用。

**理由**: backfill --corrections の遡及スキャンが英語/Guardrail パターンも検出するようになる。claude-reflect の `--scan-history` の役割を吸収。

**ツール拒否抽出の統合**: `backfill.py` にツール拒否（"The user doesn't want to proceed" + "the user said:" マーカー）からの correction 抽出を追加。ユーザーがツール実行を拒否した際の暗黙的修正パターンを `source: "backfill"` で記録。

### D8: hooks 統合 — correction_detect.py を強化

**選択**: correction_detect.py に以下を追加:
1. `should_include_message()` フィルタ（XMLタグ、JSON、500文字超のスキップ）
2. claude-reflect 由来の信頼度計算（長さ調整、強弱フラグ）
3. `"remember:"` パターンの例外処理（500文字超でもキャプチャ）

**claude-reflect hooks で不要になるもの**:
- `UserPromptSubmit` (capture_learning.py) → correction_detect.py に統合
- `SessionStart` (session_start_reminder.py) → 不要（通知のみの低価値機能）
- `PreCompact` (check_learnings.py) → save_state.py を拡張して corrections.jsonl のバックアップを追加
- `PostToolUse:Bash` (post_commit_reminder.py) → 不要（通知のみ）

### D9: データ移行 — learnings-queue.json → corrections.jsonl

**選択**: ワンタイムの移行スクリプト `scripts/migrate_reflect_queue.py` を提供。

**処理**:
1. `~/.claude/learnings-queue.json` を読み込み
2. 各アイテムを corrections.jsonl フォーマットに変換
3. `corrections.jsonl` に追記
4. 元ファイルを空配列 `[]` に

### D10: 新規ファイル構成

```
skills/
  reflect/
    SKILL.md                    # 対話レビュースキル
    scripts/
      reflect.py                # pending corrections の分析 + JSON 出力
scripts/
  reflect_utils.py              # 8層メモリ階層ルーティング（移植）
  lib/
    semantic_detector.py         # LLM セマンティック検証（移植）
  migrate_reflect_queue.py      # ワンタイム移行スクリプト
hooks/
  common.py                     # パターン統合（変更）
  correction_detect.py           # should_include_message + 全パターン処理（変更）
```

### D11: ツールエラー抽出（将来拡張）

**選択**: 初回リリースには含めない。将来 evolve の新フェーズまたは独立スキルとして実装。

**理由**: ツールエラー抽出（connection_refused, env_undefined 等）は /reflect の責務と独立性が高い。/reflect に混ぜると責務が曖昧になるため分離する。

**将来実装時の設計メモ**: `scripts/lib/tool_errors.py` として `extract_tool_errors()`, `aggregate_tool_errors()` を移植。8種エラーカテゴリ、信頼度スケーリング (2回→0.70, 3+→0.85, 5+→0.90)、`decay_days: 180`。

### D12: corrections.jsonl のクリーンアップ — prune.py に統合

**選択**: prune.py の実行時に corrections.jsonl の `applied`/`skipped` レコードのうち `decay_days` を超過したものを削除する。

**理由**: corrections.jsonl は `applied`/`skipped` レコードが永遠に蓄積する。数ヶ月〜数年で数千行になり、`/reflect` の読み込み・パースが遅くなる。

**クリーンアップ条件**:
- `reflect_status` が `"applied"` または `"skipped"`
- `timestamp` + `decay_days` が現在日時を超過
- `pending` レコードは削除しない

**実行タイミング**: evolve パイプラインの Prune フェーズで既存の prune 処理と一緒に実行。

### D13: project_path の null ハンドリング

**選択**: `CLAUDE_PROJECT_DIR` 環境変数が未設定の場合、`project_path` は `null` で記録する。

**フィルタリング時の挙動**:
- `project_path: null` の correction は「プロジェクト不明」として global-looking 扱い
- `/reflect` 時に「プロジェクト情報なし: global として扱います」と表示

## Risks / Trade-offs

- **[パターンの偽陽性増加]** → claude-reflect の FALSE_POSITIVE_PATTERNS 7個を統合。セマンティック検証をオプションで利用可能にし、`/reflect` 時に LLM で二次フィルタ
- **[claude -p の呼び出しレイテンシ]** → 全 pending corrections を1回の `claude -p` でバッチ送信しレイテンシを最小化。`--skip-semantic` で無効化可能
- **[claude-reflect アップストリーム追従喪失]** → claude-reflect v3.0.1 は安定版。ユーザーが CJK/パイプライン統合で独自路線を進んでおり、アップストリーム追従のメリットが薄い
- **[データ移行の失敗]** → migrate_reflect_queue.py は冪等（同一レコードの二重追記防止）。元データは削除せず空配列化のみ
- **[evolve-enrich-reorganize との衝突]** → 本 change は evolve-enrich-reorganize 完了後に実装。evolve.py の Phase 追加は末尾（Reflect Step）のみで、Enrich/Reorganize とは独立
- **[corrections.jsonl の肥大化]** → D12 で prune.py にクリーンアップを統合。`applied`/`skipped` の `decay_days` 超過レコードを定期削除
