# レポートのナレーション指示（一言メモ + クライマックス）

evolve は世界観（Step 0.5）に沿って各ステージ完了後に短い一言メモを出す。
これは flavor（演出）であり主機能ではない。スクリプトが利用できない場合はスキップしてよい。
SKILL.md 各 Step の「一言メモ → references/report-narration.md」ポインタからここを参照する。

## 各ステージ完了後の一言メモ

**Discover / Diagnose 完了後**（発見パターン数 = `unmatched_patterns` + `matched_skills`）:
- 3件以上: 「{N}件の兆候を確認。一つずつ見ていく。」
- 1〜2件: 「{N}件、気になる点あり。見落とさないようにする。」
- 0件: 「問題なし。今日は静かな日だ。」

**Remediation 完了後**（N = N件修正の数）:
- 3件以上: 「{N}件修正。地道な仕事だ。」
- 1〜2件: 「{N}件、小さな修正。でも確かな改善だ。」
- 0件: 「今回は何も変えなかった。それでいい。」

**Prune / Housekeeping 完了後**:
- 「整理完了。少し軽くなった。」

**自己解析（Step 11）完了後**（起票件数）:
- 1件以上: 「自分の歪みを {N} 件、記録に残した。次はそこから直る。」
- 0件（候補ありだが全却下/全重複）: 「気づきはあったが、今は起票しない。それも判断だ。」
- 候補ゼロ: 「自分を省みた。問題なし。」

## Report クライマックス（成長レベル）

evolve.py の出力 JSON のトップレベル `result["env_score"]` は**構造化 dict**（#523-2/#526-2）。
スクリプトが既に `compute_level` を解決済みなので、再計算は不要でこの dict をそのまま読む:

```json
// 成功時
{"score": 0.72, "level": 7, "title_ja": "熟達", "title_en": "Experienced",
 "sources": ["coherence", "telemetry"], "degraded": false}
// 算出失敗時（silence != evaluated: 黙らず degraded を出す）
{"score": null, "degraded": true, "reason": "...",
 "previous_level": 6, "previous_title_ja": "..."}
```

- `degraded` が false: `score` / `level` / `title_ja` / `title_en` をそのまま使う（`<ENV_SCORE>` = `score`）。
- `degraded` が true: 「env_score: 取得失敗（前回 Lv.{previous_level}・world-context.json から）」と
  1 行で surface する。黙って表示なしにはしない（取得失敗を観測可能にするのが原則）。

次に成功時のみ `save_world_context` で world-context.json に保存する（`<ENV_SCORE>` =
`result["env_score"]["score"]`。`SLUG` は Step 0.5 と同じ PJ 別スコープ値。
slug は env 経由で渡す＝python -c へ直接埋め込むと repo 名に `'` を含む場合に壊れる）:

```bash
SLUG="$(basename $(git rev-parse --show-toplevel 2>/dev/null || echo unknown))"
SLUG="$SLUG" python3 -c "
import sys, os; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts/lib')
from world_context import load_world_context, save_world_context
from pathlib import Path
data_dir = Path(os.environ.get('CLAUDE_PLUGIN_DATA', Path.home() / '.claude' / 'evolve-anything'))
slug = os.environ['SLUG']
ctx = load_world_context(data_dir, slug) or {}
save_world_context(data_dir, ctx, env_score=<ENV_SCORE>, slug=slug)
"
```

`previous_level` / `current_level` は `save_world_context` が自動更新する。更新後の値でナレーションを出力する:

- レベルアップ（`previous_level` < `current_level`、かつ両方あり）:
  「✨ {旧称号} → **[Lv.{current_level}] {新称号}**」
- 変化なし（`previous_level` == `current_level`、かつ値あり）:
  「**[Lv.{current_level}] {称号}**」
- 前回レベル不明（`previous_level` == null / 初回）:
  「**[Lv.{current_level}] {称号}**」
- `env_score.degraded` が true（取得失敗）: 上記 degraded の 1 行を出す（**表示なしにはしない**）。
