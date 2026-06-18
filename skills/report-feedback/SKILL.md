---
name: report-feedback
effort: low
description: |
  evolve / audit のレポートを「上位の目」でメタレビューし、rl-anything プラグイン
  自体の改善点・バグ・提案を抽出して todoroki-godai/rl-anything に GitHub issue 起票する。
  他PJで /rl-anything:evolve や /rl-anything:audit を回した直後、その会話で
  「レポート見て改善点ある？」「rl-anything のバグっぽい所は？」「これ issue にして」
  と言われたら必ずこのスキルを使う。決定論 self-analysis（evolve_introspect）が拾えない
  「人が読んで気づく」種類の改善（表示の分かりにくさ・提案の質・誤検知・UX）が対象。
  会話から直接 rl-anything へフィードバック/バグ報告したいときも、このスキルで起票する
  （旧 feedback スキルの後継）。
  Trigger: report-feedback, レポート見て改善, evolve レポートレビュー, audit レポートレビュー,
  rl-anything 改善点, rl-anything バグ, メタレビュー, feedback, フィードバック, バグ報告,
  bug report, 機能提案, feature request, これ issue にして
---

# /rl-anything:report-feedback — レポートからの改善フィードバック起票

evolve / audit のレポートを LLM がメタレビューし、**rl-anything プラグイン自身**への
改善 issue を `todoroki-godai/rl-anything` に半自動起票する。会話からの直接フィードバックにも対応する。

## 何を見るスキルか（最重要・取り違え防止）

evolve/audit のレポートは **対象環境（他PJのスキル/ルール）の改善** を出すもので、その**中身（結論）は
rl-anything 自身の話ではない**。このスキルは中身を report してはいけない。見るのは **レポートの出来栄え・
挙動** — レポートを鏡にして **道具（rl-anything）自身**を映す:

- レポートの**作り**: 数字の母数が無い / 単位不明 / 読み解けない表示
- パイプラインの**挙動**: 同じ提案が毎回出る / 誤検知が多い / 提案が的外れ
- **バグ**: セクションでクラッシュ / 例外の握り潰し / 矛盾した提案
- **UX/設計**: こうした方が使いやすい / フローの摩擦

判定の物差し: 「これは rl-anything のコード/挙動を直せば良くなるか？」が Yes なら対象。
「対象PJのスキルを直す話」なら **対象外**（それは evolve 本体が既にやる）。

## なぜこのスキルがあるか

evolve には既に `evolve_introspect`（Step 11）があり、result dict を **決定論で** 解析して
issue 候補を出す。だがそれは機械が拾える矛盾だけ（split↔archive 矛盾、line budget 悪化提案など）。
**「レポートを読んで初めて気づくこと」** — 表示が分かりにくい、数字の意味が不明、提案の質が悪い、
誤検知（FP）が多い、こうした方が良いという UX/設計の気づき — は決定論では拾えない。
このスキルはそこを **LLM のメタレビュー** で埋め、`evolve_introspect` の dedup/起票配線を再利用する。

## 2つの経路

| 経路 | 起動文脈 | 入力 |
|------|----------|------|
| **レポート経路**（主目的） | 他PJで evolve/audit を回した直後 | 会話に出ているレポート ＋ あれば最新の result JSON |
| **会話経路**（旧 feedback 後継） | 会話中に rl-anything のバグ/要望に気づいた | 直近の会話コンテキスト |

どちらも候補 → dedup → 人間承認 → 起票、の同じ後段に合流する。

## 実行手順

### Step 1: 認証チェックと起票先固定

```bash
rl-usage-log "report-feedback"
gh auth status 2>&1 | head -3
```

- 起票先は **常に `todoroki-godai/rl-anything`**（検出対象はパイプライン自身。どのPJで動いても固定）。
- 未認証なら候補を `~/.claude/rl-anything/feedback-drafts/` にローカル保存し（MUST）、
  「`gh auth login` 後に再実行」と案内して終了する。

### Step 2: レポート（と self-analysis）を取得

**レポート経路**: 直前の会話に出ている evolve/audit のレポート本文を入力にする。レポートの
**出力形態はコマンドで違う**ので、入力の取り方を間違えないこと:

- **audit 経路**: `rl-audit` は **stdout にレポートを出すだけ**（markdown/json 出力フラグも
  `self_analysis` も無い）。なので入力は **会話に出ている stdout レポート本文そのもの**。
  決定論 seed は無いので、この後の self_analysis 取り込みは **スキップ**して Step 3 へ。
- **evolve 経路**: evolve が `--output <path>` で result JSON を吐いていれば、その `self_analysis`
  （決定論候補の土台）を併せて読む。最新の result を探す:

```bash
ls -t ~/.claude/rl-anything/evolve-results/*.json 2>/dev/null | head -1
```

evolve 経路で self_analysis があれば、決定論候補を **土台として** 先に取り込む（${CLAUDE_PLUGIN_ROOT} 経由）:

```bash
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/scripts/lib" python3 -c '
import json, sys
from evolve_introspect import flatten_candidates, summary_lines
result = json.load(open(sys.argv[1]))
analysis = result.get("self_analysis", {})
print("\n".join(summary_lines(analysis)) if analysis else "(self_analysis なし)")
json.dump(flatten_candidates(analysis), open("/tmp/rf_seed.json","w"), ensure_ascii=False)
' "<result.json path>"
```

**会話経路**: result JSON は無いので Step 2 はスキップし、会話コンテキストをそのまま Step 3 に渡す。

### Step 3: LLM メタレビュー → 候補生成

レポート（または会話）を読み、rl-anything **自身**への改善候補を抽出する。
観点（網羅的に当てる — 1つの観点で 0 件でも他を必ず見る）:

- **表示・可読性**: 数字の意味が不明 / 単位や母数が書かれていない / レポートが読み解けない
- **提案の質**: 誤検知（FP）が多い / 同じ提案が毎回出る / 提案が的外れ / 重複
- **バグ・矛盾**: クラッシュ・例外の握り潰し / 矛盾した提案 / 数字が明らかにおかしい
- **UX・設計**: こうした方が使いやすい / フロー上の摩擦 / 足りない機能

各候補は `evolve_introspect` の candidate スキーマに**合わせる**（後段の dedup/起票がそのまま動く）:

```json
{
  "category": "self_detection | runtime_errors | improvement_opportunities",
  "title": "簡潔な1行（重複判定に使われる）",
  "body": "## 背景\n...\n## 提案\n...\n## 根拠（レポートの該当箇所）\n...",
  "suggested_label": "bug | enhancement | feedback",
  "dedup_key": "root-cause-を表す安定slug（例: evolve-report-missing-denominator）",
  "severity": "low | medium | high"
}
```

`dedup_key` は **root cause 単位**で安定させる（毎回の起票で同じ問題が重複しないため）。
決定論 introspect はキーを機械生成するが、ここは LLM が付けるのでブレると重複防止が効かない。
ブレを抑えるため次を守る:

- **原因で命名し、症状・件数・日付を入れない**（例: ✅ `evolve-report-missing-denominator` /
  ❌ `evolve-shows-3-of-7-unclear`）。同じ原因なら毎回必ず同じキーになる粒度にする。
- 英小文字 + ハイフンの短い slug（2〜5 語）。`コンポーネント名-問題` の形を基本にする。
- 同じ root cause を別 dedup_key で二重起票しないか、Step 4 の既存 issue タイトルとも見比べる。

Step 2 の `/tmp/rf_seed.json`（決定論の土台）があれば、それと自分の候補を 1 リストに統合し、
**統合した候補リストを Write ツールで `/tmp/rf_candidates.json` に保存する**（Step 4 がこのファイルを読む）。
seed が無い会話経路でも、生成した候補を同じく `/tmp/rf_candidates.json` に保存する。

### Step 4: 既存 issue と dedup

open issue を取得し、`filter_duplicates` で重複を除く（${CLAUDE_PLUGIN_ROOT} 経由）:

```bash
gh issue list --repo todoroki-godai/rl-anything --state open --limit 200 \
  --json number,title,body > /tmp/rf_existing.json
```

```bash
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/scripts/lib" python3 -c '
import json
from evolve_introspect import filter_duplicates
cands = json.load(open("/tmp/rf_candidates.json"))      # Step 3 で書き出した候補
existing = json.load(open("/tmp/rf_existing.json"))
res = filter_duplicates(cands, existing)
json.dump(res, open("/tmp/rf_filtered.json","w"), ensure_ascii=False)
for d in res["duplicates"]:
    print(f"  重複→ #{d[\"existing_number\"]} とスキップ（{d[\"reason\"]}）: {d[\"title\"]}")
print(f"unique: {len(res[\"unique\"])} 件 / duplicates: {len(res[\"duplicates\"])} 件")
'
```

duplicates は「既存 #N と重複 — スキップ」と1行ずつ表示する（沈黙させない）。

### Step 5: 人間承認（個別）

`unique` 候補を 1 件ずつ要約してユーザーに提示し、AskUserQuestion で **起票 / 修正 / 却下** を個別に選ばせる（MUST）。
ユーザーが却下した候補は起票しない。件数が多いときは severity 高い順に並べる。

**起票前プライバシーチェック（MUST — public repo なので機械的防止が無く、ここが最後の砦）**:
提示前に各候補の `title`/`body` を自分で読み返し、対象PJ固有語が混入していないか確認する。
混入していたら**起票せず先に書き換える**:

- PJ 名・ローカルパス・対象PJのスキル名/ルール本文・ビジネス固有名 → rl-anything の挙動として一般化
  （例: 「docs-platform の foo スキルで…」→「特定スキルの評価で…」）。
- evolve レポートの数値はそのまま転記せず「母数が表示されない」のように**現象**として書く。
- 判断に迷う固有語が1つでも残るなら、その候補はユーザー提示時に「固有語を含む可能性」と添えて確認する。

### Step 6: 起票（承認分のみ）

承認された候補だけ、`render_issue_body`（重複防止マーカー付き）で body を生成して起票する:

```bash
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/scripts/lib" python3 -c '
import json
from evolve_introspect import render_issue_body
cand = json.load(open("/tmp/rf_one.json"))   # 承認された1候補
print(render_issue_body(cand))
' > /tmp/rf_body.md

gh issue create --repo todoroki-godai/rl-anything \
  --title "[report-feedback] {title}" \
  --body-file /tmp/rf_body.md \
  --label "{suggested_label}"
```

起票後、issue URL をユーザーに表示する。失敗時は `~/.claude/rl-anything/feedback-drafts/` に
タイムスタンプ付きで保存してフォールバックする。

## プライバシー（MUST NOT — 起票先は public repo）

- **対象PJ固有の情報を含めない**: PJ 名・ローカルファイルパス・対象PJのスキル名やルール本文・
  ビジネス情報。報告対象は **rl-anything の挙動**であって、それが動いた相手PJの中身ではない。
- SKILL.md の内容そのものを貼り付けない。
- 迷ったら「rl-anything のどの挙動が問題か」に一般化して書く。

## allowed-tools

Read, Bash, AskUserQuestion, Write, Glob

## Tags

report-feedback, feedback, issue, github, evolve, audit, meta-review, introspect
