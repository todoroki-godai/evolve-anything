# 自己解析 → issue 半自動起票（Step 11 詳細・#299）

evolve は他フェーズで対象 PJ を改善するが、**evolve 自身の実行結果**（提案の質・実行時エラー・改善余地）を
振り返る経路がこれまで無かった。パイプラインのバグや改善余地は人間が気づいて手で issue を立てるまで構造に残らない
（「install ≠ enforcement」と同型の配線漏れ）。このステップで evolve の `result` を自己解析し、検出した候補を
**人間承認のうえ GitHub issue 化**してメタ層のループを閉じる。

evolve.py 出力のトップレベル `self_analysis` フィールドを読む（`analyze_evolve_result` が決定論で生成済み。
LLM 非依存なのでトークン見積もりは不要）。構造:

```
self_analysis: {
  self_detection:          {candidates: [...], summary_line: "..."},   # 提案の質（split↔archive 矛盾 / line budget 悪化提案 / auto_fixable への FP landing #341）
  runtime_errors:          {candidates: [...], summary_line: "..."},   # 握り潰された phase 例外 / observability 取得失敗 / stderr 警告（scipy RuntimeWarning(NaN) 等 #341）
  improvement_opportunities: {candidates: [...], summary_line: "..."}, # 系統的却下 type / calibration regression / 整合性 drift（契約乖離・usage↔suitability 矛盾 #377-5）
  total_candidates: N,
}
```

各 candidate: `{category, title, body, suggested_label, dedup_key, severity}`。

## 1. surface（必ず3カテゴリとも — MUST）

各カテゴリの `summary_line` をそのまま列挙する。0 件でも `✓ 評価したが該当なし` 行を省略しない（silence ≠ evaluated）。
`self_analysis` が `{"error": ...}` の場合はエラーをそのまま表示。

```
### 自己解析（evolve メタ層）
- 自己検出: {self_detection.summary_line}
- 実行時エラー: {runtime_errors.summary_line}
- 改善余地: {improvement_opportunities.summary_line}
```

## 2. 候補ゼロなら終了

`total_candidates == 0` ならここで終了（上記 ✓ 3行のみ残す）。

## 3. dedup（候補がある場合 — MUST）

既存 issue（open + closed 両方）と突合し、毎 evolve の重複起票を防ぐ（root cause 単位）。
closed も取るのは、過去に直した issue の再発（regression）を検出し前歴へ backlink するため（#33）。

```bash
gh issue list --repo todoroki-godai/evolve-anything --state all --json number,title,body,state --limit 200
```

候補の flatten（3カテゴリ取りこぼし防止）と dedup は決定論ヘルパーに任せる。`self_analysis` 全体を
`/tmp/rl_self_analysis.json` に、`gh issue list` 出力を `/tmp/rl_existing_issues.json` に書き出してから:

```bash
python3 -c "
import sys, json; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts/lib')
from evolve_introspect import flatten_candidates, filter_duplicates
analysis = json.load(open('/tmp/rl_self_analysis.json'))   # result['self_analysis'] そのまま
existing = json.load(open('/tmp/rl_existing_issues.json'))  # gh issue list の出力
cands = flatten_candidates(analysis)                        # 3カテゴリを決定論で平坦化
print(json.dumps(filter_duplicates(cands, existing), ensure_ascii=False))
"
```

duplicates は「既存 #N と重複 — スキップ」と1行ずつ表示する（沈黙させない）。
`regressions`（前回 closed と同一マーカーの再発・`unique` にも残る）は「⚠️ 再発→ #N（前回 closed）」と
表示し、起票時は `render_regression_body(cand, N)` で body 冒頭に backlink を入れる（#33）。

## 4. 承認（unique のみ — 提案詳細プロトコルに従う・MUST）

unique 候補を**1件ずつ**「対象（title）・根拠（body の要点・severity）・起票先（todoroki-godai/evolve-anything）・
ラベル（suggested_label）」を提示してから AskUserQuestion で個別承認する。`suggested_label`
（runtime_error/self_detection → `bug`、improvement → `enhancement`）は提案値であり、ユーザーがラベル変更・
スキップを選べるようにする。10 件超は per-item 10 件まで展開し残りは件数で示す。

## 5. 起票（承認分のみ・MUST）

body はマーカー付きで生成する（次回 evolve が同じ root cause を確実に dedup できる）。

```bash
BODY=$(python3 -c "
import sys, json; sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts/lib')
from evolve_introspect import render_issue_body
print(render_issue_body(json.load(open('/tmp/rl_one_candidate.json'))))
")
gh issue create --repo todoroki-godai/evolve-anything --title "<title>" --body "$BODY" --label "<label>"
```
