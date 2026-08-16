# #475 実測ログ（2026-08-17・codex cold review 2巡目 [Must]4/5/6 対応）

本ファイルは `docs/decisions/drafts/475-adoption-to-rule-routing.md` §1.1 の M0〜M5 を
**単一の連続実行**（1プロセス内・同一 Python セッション）で取得した、コマンドと出力の**完全な写し**。

## なぜこのファイルが要るか

`~/.claude/evolve-anything/corrections.jsonl` は生きたストアで、セッションが進むたびに増える
（初版 172 行 → 2巡目レビュー時点で 175 行 → 本ファイル取得時点で 177 行）。
sha256 だけでは「いつの時点の値か」の指紋にしかならず、**別マシン・別時点では同じコマンドを
再実行しても同じ数字は出ない**（codex cold review 2巡目 [Must]5）。

**この repo には生ストアのコピーは置かない**（`corrections.jsonl` の各レコードは
`project_path` に個人ホームディレクトリの絶対パスを含み、`message` に実際の会話の引用を含む。
将来 public 化する可能性がある repo に生の会話ログを永続コピーするリスクの方が、
「別マシンで bit-exact 再現できない」という制約より大きいと判断した）。
代わりに、**このマシンで実際に実行したコマンドと、その verbatim 出力**を固定する。
本文書に書かれた集計値（件数・比率・confidence 分布）はすべてここから来ている。

再実行する場合は本文書内の各コマンドをそのまま使えばよいが、ストアが増えているため
**同じ数字にはならない**（構造的な結論 — 96.4% が `None` になる・confidence が 0.85 に
集約する、等 — は変わらないはずだが、絶対件数は変わる）。

## M0. 測定時点の指紋

```bash
python3 - <<'PY'
import hashlib, pathlib
p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
b = p.read_bytes()
print("lines:", len(b.decode("utf-8").splitlines()))
print("bytes:", len(b))
print("sha256:", hashlib.sha256(b).hexdigest())
PY
```

出力（verbatim・2026-08-17）:

```
lines: 177
bytes: 227870
sha256: 895e905a6575376d7af91a867b24636c6bc431d788aa36e655ad2e0eb4926be0
```

## M1. corrections.jsonl の分布（本文書 §1.1 の M1 と同一コマンド）

出力（verbatim）:

```
total 177
reflect_status: [('applied', 168), ('skipped', 8), ('pending', 1)]
source: [('reflect_confirmed', 167), ('backfill', 8), ('hook', 2)]
cross: [(('reflect_confirmed', 'applied'), 167), (('backfill', 'skipped'), 8), (('hook', 'applied'), 1), (('hook', 'pending'), 1)]
```

## M2. reflect_confirmed の日付分布と経過日数（本文書 §1.1 の M2 と同一コマンド）

出力（verbatim）:

```
reflect_confirmed: 167
by date: [('2026-06-12', 41), ('2026-06-18', 40), ('2026-07-09', 17), ('2026-07-06', 13),
          ('2026-08-13', 11), ('2026-07-30', 8), ('2026-07-16', 7), ('2026-08-16', 6),
          ('2026-07-22', 5), ('2026-07-31', 5), ('2026-08-12', 5), ('2026-08-14', 5),
          ('2026-08-15', 3), ('2026-07-17', 1)]
oldest/newest age(days): 65 / 0
older than 90d (= prune 対象): 0
```

## M3. 戻せる採用（revert 可能な optimize_history entry）の件数（本文書 §1.1 の M3 と同一コマンド）

出力（verbatim）:

```
optimize_history entries: 42 / with revert payload: 1
```

（初版・2巡目とも同じ 42/1。`corrections.jsonl` は増え続けるが `optimize_history` はこの間
新規採用が無かったため件数据え置き）

## M5（新設・P23 の再現手順・codex cold review 2巡目 [Must]6）

P23 は当初「実行コマンドが無く再現できない」という指摘を受けた。以下がその実行コマンドと
verbatim 出力。**`project_root` は本リポジトリの cwd に固定**する
（reflect の実運用では `route_corrections(pending, project_root)` が呼び出し元 PJ の
1つの `project_root` を全 pending に対して使う。corrections の `project_path` が
指す個別プロジェクトを都度切り替えて呼ぶ経路は存在しない — `skills/reflect/scripts/reflect.py:1044`）。

```bash
python3 - <<'PY'
import json, pathlib, collections, sys
sys.path.insert(0, 'scripts')
from lib.reflect_routing import suggest_claude_file

p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
rc = [r for r in recs if r.get("source") == "reflect_confirmed"]

root = pathlib.Path.cwd()  # このリポジトリ（reflect の実呼び出しと同じ固定 project_root）
none_count = 0
conf_counter = collections.Counter()
targets = collections.Counter()
for r in rc:
    result = suggest_claude_file(r, project_root=root)
    if result is None:
        none_count += 1
    else:
        path, conf = result
        conf_counter[conf] += 1
        targets[path] += 1

print("reflect_confirmed count:", len(rc))
print("None:", none_count, f"{none_count/len(rc)*100:.1f}%")
print("non-None:", len(rc) - none_count)
print("confidence dist (non-None):", conf_counter.most_common())
print("target paths:", targets.most_common())
PY
```

出力（verbatim・2026-08-17。個人パスは repo 相対に置換）:

```
reflect_confirmed count: 167
None: 161 96.4%
non-None: 6
confidence dist (non-None): [(0.85, 6)]
target paths: [('<HOME>/.claude/CLAUDE.md', 3), ('<repo>/.claude/rules/project-specific.md', 3)]
```

**codex cold review 2巡目の再測定（`167件中 None 161件、残る6件の返却confidenceはすべて0.85`）と
一致することを確認した。** 当初文書の「confidence 全件 0.9 固定」は誤りで、これは
`correction_semantic/promote.py:367` が correction レコードに書き込む**入力側**の
`confidence: 0.9`（固定値）であり、`suggest_claude_file` が**返す**マッチ confidence
（0.90 / 0.88 / 0.85 / 0.80 / 0.75 / 0.60 のいずれか、`scripts/lib/reflect_routing.py:127-186`）
とは別物。今回の 167 件では、たまたま `confidence < 0.75` の分岐（auto-memory フォールバック・
0.60）に到達する前に他の分岐へ収束せず None になるか、`.claude/rules/project-specific.md` /
`~/.claude/CLAUDE.md` にマッチする（0.85）かのどちらかしか実際には発火しなかった。

## Should-12（U8）: 5問分の表示サンプルの生成に使ったコマンド（codex 3巡目 [Must]B-3 で拡充）

`docs/decisions/drafts/475-adoption-to-rule-routing.md` §11-A に転記した5問は、
2026-08-14〜16 の実 `reflect_confirmed` レコードから message を引用し（個人パス等は
含まない短い日本語の指摘文のみ）、`draft_line` は agent 役として著者が起草した。
文字数カウントは Python `len()`（zenkaku/hankaku を区別しない素の文字数）。

**当初版（B-3 指摘: 「実際のコマンド・入力・5件それぞれの出力が artifact に無い」）を受けて、
実行したコマンドと5件個別の出力をそのまま以下に転記する。**

```bash
python3 - <<'PY'
def trunc(s, n=60):
    return s if len(s) <= n else s[:n] + "…"

# message: reflect_confirmed の実レコードから短い引用のみ抽出（個人パス等は含まない）
# draft_line: agent 役として著者が起草（実装が実際にこう分類する保証はない）
samples = [
    ("spec-keeperはcodexレビューいらない",
     "spec-keeper 等 docs-only の PR は codex レビュー不要（コード変更 PR のみ codex を標準挿入する）"),
    ("フルスイートでなんで頭でまわしちゃったの？token無駄じゃん",
     "並行 worker には python3 -m pytest -n 0（直列）を指示する。フルスイートを頭で回さない"),
    ("選べる道のメリット、デメリット考えて材料がそろってから提案してほしい。codexにも相談してみて",
     "（既存の explain-clearly.md と重複のため追記不要）"),
    ("自動でルール化を全部ユーザーにrule化するか確認すれば誤爆はふせげるよね。rule, skill, hookは"
     "ユーザーがいまは全部確認すれば良いと思う。",
     "（本設計 #475 自体がこの指摘の実装なので pitfall として経緯のみ残す）"),
    ("なんで、V5がOKっていってたの？純粋に未着手が５件もあるのに",
     "（特定タスクの一回性の指摘のため一般化しない）"),
]

# §4.3 の実際の label/detail 文言（tacchi 2巡目書き換え後）
opts_labels = ["共通ルールに書く（全PJで効く）", "このPJのルールに書く",
               "いまは反映しない（記録は残す）", "いいえ（この指摘は不要）"]
opts_detail = [
    "次のセッションから全プロジェクトで効きます。あとで1コマンドで取り消せます（条件は反映時に表示）。",
    "次のセッションからこのプロジェクトだけで効きます。取り消しも同様です。",
    "動作は変わりません。記録は消えず、5件たまったら見直しをまとめて案内します。",
    "記録も反映もしません。次回から出しません。",
]
other_hint = "メモや落とし穴集に残したい場合は Other に記入してください。"

for i, (m, dl) in enumerate(samples, 1):
    mt, dlt = trunc(m), trunc(dl)
    q_body = f"「{mt}」（1回）\n\n書く文面（案）: {dlt}\n\nこの指摘を、どこに反映しますか？\n{other_hint}"
    labels_block = "\n".join(opts_labels)
    full_block = "\n".join(opts_labels) + "\n" + "\n".join(opts_detail)
    print(f"Q{i}: body_only={len(q_body)} "
          f"body_labels={len(q_body + chr(10) + labels_block)} "
          f"full={len(q_body + chr(10) + full_block)}")
PY
```

出力（verbatim・2026-08-17）:

```
Q1: body_only=155 body_labels=213 full=360
Q2: body_only=158 body_labels=216 full=363
Q3: body_only=152 body_labels=210 full=357
Q4: body_only=172 body_labels=230 full=377
Q5: body_only=123 body_labels=181 full=328
```

平均・最大は本文（§11-A）のとおり: body_only 平均152/最大172、body_labels 平均210/最大230、
full 平均357/最大377（いずれも400字以内）。

**Q1（`full`）の実際のテキスト**（第三者が再計算できるよう1件だけそのまま転記する。
他4件も上記スクリプトで同じ形式が再現できる）:

```
「spec-keeperはcodexレビューいらない」（1回）

書く文面（案）: spec-keeper 等 docs-only の PR は codex レビュー不要（コード変更 PR のみ code…

この指摘を、どこに反映しますか？
メモや落とし穴集に残したい場合は Other に記入してください。
共通ルールに書く（全PJで効く）
このPJのルールに書く
いまは反映しない（記録は残す）
いいえ（この指摘は不要）
次のセッションから全プロジェクトで効きます。あとで1コマンドで取り消せます（条件は反映時に表示）。
次のセッションからこのプロジェクトだけで効きます。取り消しも同様です。
動作は変わりません。記録は消えず、5件たまったら見直しをまとめて案内します。
記録も反映もしません。次回から出しません。
```

（文字数360は上記テキスト全体の `len()`。実際の AskUserQuestion UI のレイアウト・改行・
区切り記号の描画は tool 側の表示仕様次第で変わりうるため、この字数は「入力として渡すテキスト量」
の proxy であり、画面上の見た目の行数そのものではない — この限界は §11 U9 の観測対象。）
