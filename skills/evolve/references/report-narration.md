# レポートのナレーション指示（一言メモ + クライマックス）

evolve は世界観（Step 0.5）に沿って各ステージ完了後に短い一言メモを出す。
これは flavor（演出）であり主機能ではない。スクリプトが利用できない場合はスキップしてよい。
SKILL.md 各 Step の「一言メモ → references/report-narration.md」ポインタからここを参照する。

`$PJ` は対象 PJ の絶対パス（束縛パターン: `PJ="${PJ:-$(pwd)}"`。env の `PJ` があれば優先・無ければ cwd。バッチ経路 #400 本体では呼び出し側が `PJ` を env で渡すだけで対応できる）。本ファイルの `$PJ` 言及は SKILL.md Step 6.2 で既に実行済みのコマンドの**出力フィールドを指す説明**であり、新たな実行を指示するものではない（新規実行を伴う手順は SKILL.md / correction-review.md 側にあり、いずれも同じ行・同一コードブロック内で束縛済み）。

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
`result["env_score"]["score"]`。bash は呼び出しごとに独立プロセスのため `$PJ`/`$SLUG` は
Step 0.5 の値を前提にせずこのブロックで自前に再導出する（プロセスをまたいだ前提は置かない）。
slug は env 経由で渡す＝python -c へ直接埋め込むと repo 名に `'` を含む場合に壊れる）:

```bash
PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス（Step 0.5 と同一の束縛パターン）
# resolve_slug（git-common-dir 親, ADR-031）— worktree でも本体 PJ slug に正規化。
# 旧実装（basename $(git rev-parse --show-toplevel)）は cwd 依存かつ worktree で本体と
# 食い違う別導出だったため、SKILL.md/世界観ロードと同じ resolve_slug に統一した（#400 round5）。
SLUG="$(PJ="$PJ" python3 -c "import os, sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts/lib'); from optimize_history_store import resolve_slug; print(resolve_slug(cwd=os.environ['PJ']))" 2>/dev/null || echo unknown)"
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

## Step 9: Report フェーズの詳細フォーマット

evolve の結果を**人間が読みやすい形式**で出力する。raw な audit テキストをコードブロックにそのまま貼り付けてはならない（MUST NOT）。

**TL;DR を冒頭に必ず出す（MUST・#525-2）**: レポートの一番上に、3 つの数字を1行で出す。詳細セクションを全部読まなくても「今回の evolve で何が起きたか」が即わかるようにするため。

```
TL;DR: 変更 {N} 件 / 要対応 {M} 件 / 残りすべて評価済みクリーン
```

- **変更 N 件**: 今回 evolve で実際にファイルへ適用した件数（skill diff / remediation fix / memory 書込 / 昇格など、apply 実績の合算）。dry-run 分析のみで何も適用していなければ 0。
- **要対応 M 件**: Step 10 の「🔴 要対応（実行コマンドあり）」の件数。
- **残りすべて評価済みクリーン**: 上記以外の observability 項目（全 ✓ のもの）。

**全 ✓ の observability 項目は1ブロックに畳む（MUST・#525-2）**: Step 3.8 で surface する observability の各 key のうち、「✓ クリーン（該当なし / drift なし）」だけのものは個別に1行ずつ展開せず、まとめて1行に畳む:

```
✓ クリーン: glossary / orphan_store / store_contract / hook_drift / agent_team / measurement_bug / promotion_readiness / testpaths_coverage
```

⚠ や ℹ（要注意・データ不足・要対応）を含む項目だけ個別に1行 surface する。これにより「全部 ✓ なのに項目数だけ多くて読みづらい」を防ぐ。**畳んでよいのは clean のものだけ**で、silence != evaluated は「✓ クリーン: ...」のブロックに名前を残すことで担保する（評価したことは見える）。

**フォーマット規則（MUST）**:
- 各セクションは `###` 見出しで区切る
- 数値は「問題あり」「問題なし」の判定を添えて表示（数値だけでなく意味を伝える）
- 重大な問題がなければ「✅ 問題なし」と明示する（沈黙は禁止）
- 誤検知（スキップした理由）は「⚠ 誤検知 — スキップ: {理由}」と1行で示す

**出力例（このフォーマットに従う）**:
```
### 今回の evolve まとめ

#### アーティファクト概況
- スキル: N件（custom: X / global: Y）
- rules: Z件 / memory: W件

#### 検出された問題
- ⚠ 誤検知スキップ: stale_ref 6件（AWS SSM パス — バッククォート内のため対象外）
- ✅ rules/memory/hooks/claudemd: 問題なし

#### スキル品質
- implement: 0.88 ✅
- evolve: 0.76（要観察）
```

レポートには以下のセクションが含まれる:
- **Usage (last 30 days)**: PJ 固有スキルのみのメインランキング（プラグインスキルは除外）
- **Plugin usage**: プラグイン別の総使用回数サマリ（例: `gstack(340) / evolve-anything(30)`）
- **gstack Workflow Analytics**: gstack スキルが検出された場合、ファネル（plan→refine→ship→document→spec→retro の完走率）、フェーズ別効率、品質トレンド、最適化候補を表示
- **/simplify ゲート結果**: Step 5.6 で /simplify を実行した場合、「/simplify: N件の改善を適用」または「/simplify: 実行済み・変更なし」「/simplify: スキップ（対象なし or 未対応バージョン）」を Compile セクションに表示

## 成長状態レポート（#448）

成長レベル表示の直後に `result["growth_report"]` の `lines` を列挙する（MUST）。
`growth_report` キーが存在しない / `error` キーが含まれる場合は表示をスキップ。
`lines` が空リストの場合は「成長状態: データ不足」を 1 行表示する。
表示例:
- `corrections（human-confirmed のみ）7/10 — あと3件で構造化育成へ`
- `  └ カウントされるアクション: /reflect で approve または --promote-weak で昇格した修正（自動検出・Stop hook 由来は除外）`（#51 LOW）
- `本日累計 reflect 確認 2件 / idiom 1件 が自動化対象に昇格（このrunでは 1 件）`

**対話前スナップショット問題の補正（#476-4・MUST。全PJ値の混入を断つ — #526-1）:** `growth_report` は analysis 時点で生成されるため `corrections_human` / `promoted_today` は **Step 6.2 の対話で昇格する前の値**で固定される。Step 6.2 で実際に昇格した場合の上書きは、必ず **per-PJ の値に今回昇格数を加算する** 方式で行う（**`evolve-reflect --project-dir "$PJ" --promote-weak` 出力の `corrections_human_allpj` をそのまま使ってはならない — MUST NOT**）:

- **`corrections（human-confirmed のみ）` 行**: `result["growth_report"]["corrections_human"]`（= 当PJ analysis 時点の値）に、Step 6.2 で「はい」と答えて昇格に成功した件数を**足した値**を分子にする（分母は `corrections_target`）。
  - ⚠ **`evolve-reflect --project-dir "$PJ" --promote-weak` の出力 `corrections_human_allpj` は全PJ合計（例 41）を返す（#557 でリネーム済み）**ため、これで当PJ値（例 0/10）を上書きすると `41/10` という不整合表示になる（#526-1 の事故）。CLI 出力の `corrections_human_allpj` は当PJ分母 `/10` と意味が合わないので分子に使わない。
- **`本日累計 ...（このrunでは M 件）` 行**: growth_report の `promoted_today` / `autopromoted_today`（本日累計・store 由来）と、`promoted_this_run` / `autopromoted_this_run`（このrun・明示渡し）をそのまま使う。Step 6.2 で承認した直後で store がまだ反映前なら、このrun件数を本日累計に足して表示してよい。
- 昇格が 0 件だった場合は growth_report の値をそのまま表示する。

`corrections（human-confirmed のみ）` は reflect 承認 / idiom_dict 自動昇格のみを数えた **当PJ** の数で、prune の `corrections kept`（全 correction を数える）とも、CLI の全PJ集計とも別物（行内の `（human-confirmed のみ）` ラベルと「当PJ」スコープで区別する）。
