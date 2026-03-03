## Context

rl-anything の observe hooks はツール呼び出し（Skill/Agent）を `usage.jsonl` に記録し、discover/prune がそのデータから行動パターン検出・淘汰候補判定を行う。しかし以下の限界がある：

1. **ユーザー修正の検出不可**: 「いや、違う」「そうじゃなくて」等のフィードバックが記録されず、スキルの品質問題を検出できない
2. **時間減衰なし**: 3ヶ月前に1回使われたスキルも昨日使われたスキルも同じ重みで扱われる
3. **推奨先が単一**: analyze の出力が「evolve で改善」一択で、rules/CLAUDE.md/memory への振り分けがない

claude-reflect は UserPromptSubmit hook で CJK/英語の修正パターンを検出し、信頼度スコア + decay_days で管理し、/reflect 時に LLM 検証して複数ターゲットに振り分けるアーキテクチャを持つ。これを rl-anything に適用する。

## Goals / Non-Goals

**Goals:**
- ユーザーの修正発話を自動検出し、直前のスキル実行と紐付けて記録する
- prune 判定に時間減衰（decay）を導入し、最近使われていないスキルを適切に淘汰する
- analyze の推奨アクションを skills / rules / CLAUDE.md に振り分ける

**Non-Goals:**
- claude-reflect の学習キュー機構の再実装（claude-reflect に任せる）
- /reflect コマンドとの統合や重複
- ユーザーへの対話的な確認フロー（analyze は非対話で完結）

## Decisions

### Decision 1: UserPromptSubmit hook で修正パターンを検出

**選択**: 新規 `hooks/correction_detect.py` を UserPromptSubmit hook として追加

**代替案**:
- A) backfill で過去セッションから抽出 → リアルタイム性がない
- B) PostToolUse の tool_result からエラーを推定 → ユーザー不満とは別の指標

**理由**: claude-reflect と同じイベントだが、rl-anything は「直前の Skill 呼び出し」との紐付けが必要。`corrections.jsonl` に `{correction_type, skill_name, session_id, confidence, timestamp}` を記録。

**パターン定義**: claude-reflect の CJK_CORRECTION_PATTERNS を参考に、rl-anything 独自のパターンセットを定義。claude-reflect のコードを直接 import しない（プラグイン間依存は避ける）。

```python
CORRECTION_PATTERNS = [
    (r"^いや[、,.\s]|^いや違", "iya", 0.85),
    (r"^違う[、，,.\s]", "chigau", 0.85),
    (r"そうじゃなく[てけ]", "souja-nakute", 0.80),
    (r"^no[,. ]+", "no", 0.75),
    (r"^don't\b|^do not\b", "dont", 0.75),
    (r"^stop\b|^never\b", "stop", 0.80),
]
```

### Decision 2: confidence + decay を usage レコードに後付け計算

**選択**: usage.jsonl のスキーマは変更せず、prune/analyze 実行時に decay を動的計算

**代替案**:
- A) usage.jsonl に confidence フィールドを追加 → 既存データとの互換性問題
- B) 別テーブルで管理 → 複雑化

**理由**: `confidence = base_score * exp(-age_days / decay_days)` を prune 集計時に計算。base_score はデフォルト 1.0、corrections があるスキルは減点。decay_days は 90 日（claude-reflect と同じ）。

### Decision 3: analyze の multi-target routing

**選択**: analyze の recommendation に `target` フィールドを追加

**ルーティングルール（優先度順）**:

| 優先度 | 条件 | target | アクション |
|--------|------|--------|------------|
| 1（最高） | correction_count > 0 | `"skill"` | スキル改善が必要（evolve で改善） |
| 2 | zero_invocation かつ global | `"prune"` | 削除候補（prune で淘汰） |
| 3 | 高頻度パターン（10+ 回、3+ PJ） | `"claude_md"` | CLAUDE.md への昇格 |
| 4（最低） | project 固有パターン（5+ 回、1 PJ） | `"rule"` | rules/ への追加候補 |

複数条件に該当する場合は、優先度の高い target を採用する（MUST）。

### Decision 4: backfill での corrections 遡及抽出

**選択**: backfill に `--corrections` フラグを追加。既存セッションの human messages から修正パターンを抽出し、直前の assistant ターンで使われた Skill と紐付ける。

**制約**: 過去セッションでは「直前の Skill」の特定精度が下がるため、confidence を 0.6 に下げる（リアルタイムの 0.75-0.85 より低い）。

## Risks / Trade-offs

- **[Risk] 修正パターンの誤検出** → 「いや、いいね」のような偽陽性。claude-reflect と同様に false_positive_filters（末尾「？」、CJK 疑問句末助詞）を導入。analyze 時の LLM 検証でさらにフィルタ。
- **[Risk] claude-reflect との重複検出** → 同じ UserPromptSubmit イベントで両方が発火。データ保存先が別なので問題ないが、将来的に claude-reflect の corrections データを参照する拡張パスを残す。
- **[Risk] decay が aggressive すぎて有用スキルを淘汰** → decay_days = 90 をデフォルトにしつつ、`pin` マーク機能で保護可能に（ooishi-design-system のような定義型スキル向け）。
- **[Trade-off] プラグイン間依存を避けるためパターン定義が重複** → メンテコスト増だが、プラグインの独立性を優先。
