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

## Should-12（U8）: 5問分の表示サンプルの生成に使ったコマンド

`docs/decisions/drafts/475-adoption-to-rule-routing.md` §11 U8 に転記した5問は、
2026-08-14〜16 の実 `reflect_confirmed` レコードから message を引用し（個人パス等は
含まない短い日本語の指摘文のみ）、`draft_line` は agent 役として著者が起草した。
文字数カウントは Python `len()`（zenkaku/hankaku を区別しない素の文字数。壊れて赤くする
検査は行っていない単純カウント）。実行コマンドと出力は §11 U8 の本文に文字数の表として転記済み
（サンプル本文自体は個人特定情報を含まないため本ログには複製しない）。
