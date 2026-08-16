# 475 設計ドラフト: 朝の y/n で採用した指摘を反映先まで一度に決める

- 対象 issue: **#475**（朝の y/n で「はい」した指摘がルール文書に反映される工程に届かない）
- 関連: #467（Epic・入口側）/ #379（縮小方針・新設凍結）/ #402・ADR-053（revert lane）/ #99（昇格チャネルの非対称）
- 状態: **設計ドラフト rev1（未レビュー）**。実装着手前。コードは1行も変更していない
- 前提コミット: `556d846d`（`docs/467-rev5-design-rollback`）
- 測定日: **2026-08-16**（初版）/ **2026-08-17**（codex cold review 2巡目の [Must] 反映・全実測値をこの時点に統一）

---

## 0. この設計が答える問い

1. 朝の y/n で「はい」と答えた指摘は、いま実際にどこへ行くのか（そして**どこへ行かないのか**）
2. 反映先を「1回の設問」で決めさせるには、どんな選択肢をどんな文言で出すのか
3. その選択の後、誰が・どのファイルに・どんな形式で書くのか
4. #379 の新設凍結に触れずに実現できるか
5. 壊れたときにどう気づくか（気づけないなら、どこが気づけないか）

**この設計が答えない問い**: #467 の入口側再設計（どの提案種別を朝に出すか）、#400/#401 の再設計。
参照に留める。

---

## 1. 証拠の等級（`~/.claude/rules/design-review-gate.md` の入口条件）

本文の各主張には等級を付す。混ぜて読むと「全部が同じ強さで確定している」と誤読される。

| 等級 | 内容 | 誰が再検証できるか |
|---|---|---|
| **[コード]** | 本リポジトリのソースから読み取れる事実（定数・条件分岐・呼び出しの有無） | 誰でも。file:line で追える |
| **[実測]** | このマシンの実ストア（`~/.claude/evolve-anything/*.jsonl`）を読んだ観測値 | **本人の環境でのみ**。他環境では再現しない |
| **測定不能** | 手段が無い、または本セッションから観測できない。理由を明記する | — |

### 1.1 [実測] の再現手順（取得日 2026-08-16・全ストア全量・read-only）

**ストアへの書込みは一切行っていない**（読み取りのみ。DuckDB は開いていない。LLM バッチも実行していない）。
以下はそのまま貼れば再実行できる。

> **測定時点の固定（2026-08-16 codex cold review 1巡目 [Must]・T6 実測で判明）**
> `corrections.jsonl` は**同一日内でも増える**。初版の記載値（total 172 / applied 163 /
> reflect_confirmed 162）は、同日の再測定で **175 / 166 / 165** になった（差分 +3 はいずれも
> 2026-08-16 付の新規レコード。結論に影響する値ではない）。
> **取得日だけでは時点を特定できない**ため、以後は下記 M0 のスナップショット指紋を併記する。
>
> **2026-08-17 codex cold review 2巡目 [Must]5 を受けた追加対応**: sha256 は「いつの時点の値か」の
> 指紋にしかならず、**別マシン・別時点で同じコマンドを再実行しても同じ数字は出ない**（このストアは
> 生きていて増え続ける）という指摘を受けた。`~/.claude/` 配下は書込み禁止で、かつ生ストアの
> コピーは repo に置かない（各レコードの `project_path` に個人ホームディレクトリの絶対パス、
> `message` に実際の会話引用を含み、将来 public 化しうる repo に永続コピーするリスクの方が大きいと
> 判断した）。代わりに、**このマシンで実行したコマンドと verbatim 出力の完全な写し**を
> [`artifacts/475-measurement-log-20260817.md`](artifacts/475-measurement-log-20260817.md) に固定した。
> 本文書の [実測] 値はすべてこの1回の連続実行から取得しており、**全て同一時点（下記 M0）に揃っている**。
>
> **M0. 測定時点の指紋**
>
> ```bash
> python3 - <<'PY'
> import hashlib, pathlib
> p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
> b = p.read_bytes()
> print("lines:", len(b.decode("utf-8").splitlines()))
> print("sha256:", hashlib.sha256(b).hexdigest())
> PY
> ```
>
> 本文書の [実測] 値の基準時点: **lines=177 / bytes=227870（223 KB）/
> sha256=`895e905a6575376d7af91a867b24636c6bc431d788aa36e655ad2e0eb4926be0`**（2026-08-17・
> 全出力は上記 artifact を参照）

**M1. corrections.jsonl の分布（reflect_status / source / 交差）**

```bash
python3 - <<'PY'
import json, collections, pathlib
p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
print("total", len(recs))
print("reflect_status:", collections.Counter(r.get("reflect_status","<missing>") for r in recs).most_common())
print("source:", collections.Counter(r.get("source","<missing>") for r in recs).most_common())
print("cross:", collections.Counter((r.get("source"), r.get("reflect_status")) for r in recs).most_common())
PY
```

**M2. 朝の y/n 由来レコードの日付分布と経過日数（prune 到達予測）**

```bash
python3 - <<'PY'
import json, pathlib, datetime, collections
p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
rc = [r for r in recs if r.get("source") == "reflect_confirmed"]
now = datetime.datetime.now(datetime.timezone.utc)
ages = sorted((now - datetime.datetime.fromisoformat(r["timestamp"].replace("Z","+00:00"))).days for r in rc)
print("reflect_confirmed:", len(rc))
print("by date:", collections.Counter(r["timestamp"][:10] for r in rc).most_common())
print("oldest/newest age(days):", ages[-1], "/", ages[0])
print("older than 90d (= prune 対象):", sum(1 for a in ages if a > 90))
PY
```

**M3. 戻せる採用（revert 可能な optimize_history entry）の件数**

```bash
python3 - <<'PY'
import json, pathlib
root = pathlib.Path.home()/".claude/evolve-anything/optimize_history"
tot = rev = 0
for p in sorted(root.glob("*.jsonl")):
    for l in p.read_text(encoding="utf-8").splitlines():
        if not l.strip(): continue
        r = json.loads(l); tot += 1
        if r.get("revert_schema_version"): rev += 1
print("optimize_history entries:", tot, "/ with revert payload:", rev)
PY
```

**M4. P19 が引用する個別レコードの本文**（codex [Must]: M1〜M3 のどれもレコード本文を出力しないため追加）

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
for r in recs:
    if r.get("source") == "reflect_confirmed" and r.get("timestamp","").startswith("2026-08-15"):
        print(r.get("timestamp"), "|", (r.get("message") or "")[:120])
PY
```

**M5. P23（`suggest_claude_file` の実データ dry-run）の再現手順**（codex cold review 2巡目 [Must]6:
「P23 に実行コマンドが無い」を受けて追加。`project_root` は本リポジトリの cwd に固定する —
reflect の実呼び出し `route_corrections(pending, project_root)`（`skills/reflect/scripts/reflect.py:1044`）
が単一の `project_root` を全 pending に対して使うため、各レコードの `project_path` へ都度
切り替える経路は実装に存在しない）。

```bash
python3 - <<'PY'
import json, pathlib, collections, sys
sys.path.insert(0, 'scripts')
from lib.reflect_routing import suggest_claude_file

p = pathlib.Path.home()/".claude/evolve-anything/corrections.jsonl"
recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
rc = [r for r in recs if r.get("source") == "reflect_confirmed"]

root = pathlib.Path.cwd()
none_count, conf_counter, targets = 0, collections.Counter(), collections.Counter()
for r in rc:
    result = suggest_claude_file(r, project_root=root)
    if result is None:
        none_count += 1
    else:
        path, conf = result
        conf_counter[conf] += 1
        targets[path] += 1
print("reflect_confirmed:", len(rc), "/ None:", none_count, f"({none_count/len(rc)*100:.1f}%)")
print("confidence dist:", conf_counter.most_common())
print("targets:", targets.most_common())
PY
```

出力（verbatim・全文は artifact 参照）:

```
reflect_confirmed: 167 / None: 161 (96.4%)
confidence dist: [(0.85, 6)]
targets: [('<HOME>/.claude/CLAUDE.md', 3), ('<repo>/.claude/rules/project-specific.md', 3)]
```

**[コード] 側の再現**: 凍結定数の現況は下記1行で出る（読み取りのみ）。

```bash
python3 -c "import sys;sys.path.insert(0,'scripts/lib');import shrink_freeze as s;print(s.SHRINK_FREEZE_ACTIVE,len(s.FROZEN_STORES),len(s.FROZEN_OBSERVABILITY_SECTIONS),len(s.FROZEN_ADVISORY_PROPOSAL_ADAPTERS),len(s.FROZEN_WEAK_SIGNAL_CHANNELS))"
```

---

## 2. この設計が依拠する前提と evidence（空欄なし）

計 **28 件**（[コード] 20 / [実測] 5 / 測定不能 3）。P1〜P25 の25件 + 測定不能 U1〜U3 の3件。
うち P21〜P25 は codex cold review 1巡目の [Must] を受けた追加実測（§2.6）。**内訳は2巡目の
P21/P22 再格付け（[実測]→[コード]）を反映済み**（旧内訳は「14/8/3」で合計25件しかカウントして
おらず、P21〜P25 追加後も更新されていなかった。P1〜P25 が実際には25件あるため、旧内訳の
コード14+実測8=22件は当初から3件不足していた誤記でもあった）。

### 2.1 現状の経路に関する前提

| # | 前提 | 等級 | evidence（値 / 取得元 / 取得日） |
|---|---|---|---|
| P1 | 朝の y/n の「はい」は weak_signal を corrections へ昇格し、`reflect_status: "applied"` を付ける | [コード] | `scripts/lib/correction_semantic/promote.py:371`（`"reflect_status": "applied"` リテラル）/ 2026-08-16 |
| P2 | 昇格レコードの `source` は `"reflect_confirmed"`、`routing_hint` / `extracted_learning` は `None` | [コード] | `promote.py:350`（`source` 既定値）/ `:369`（`routing_hint: None`）/ `:372`（`extracted_learning: None`） / 2026-08-16 |
| P3 | reflect の未処理抽出は `reflect_status == "pending"` のみを返す | [コード] | `scripts/lib/discover/suppression.py:196` および `skills/reflect/scripts/reflect.py:127-131`（同一条件が2箇所） / 2026-08-16 |
| P4 | ゆえに **promote 済みは反映を担う工程から構造的に不可視**（P1+P3 の合成。データではなく分岐で決まる） | [コード] | 同上 / 2026-08-16 |
| P5 | ルール文書への書込みは Python コードではなく、**SKILL.md の指示下で agent が Edit/Write で行う** | [コード] | `scripts/`・`hooks/`・`skills/` 配下に `.claude/rules/` へ書く writer は grep で **0 件**。書込み規約は `skills/reflect/SKILL.md:151-154`（approve/edit）と `:168-174`（書き込み時のルール） / 2026-08-16 |
| P6 | 反映先の機械提案 `suggest_claude_file` は **グローバル rule (`~/.claude/rules/`) を候補に持たない**。global 行きは `~/.claude/CLAUDE.md` のみ | [コード] | `scripts/lib/reflect_routing.py:108-180`。`Path.home()` 出現は :97（skills）/ :136,:140（CLAUDE.md）/ :176,:179（memory）の5箇所のみ / 2026-08-16 |
| P7 | rule ファイルの行数上限は **10 行**（`MAX_RULE_LINES`）。`validate_rule_content` の docstring は「3行以内」と書いてあるが**実装は 10**（docstring drift） | [コード] | `scripts/lib/line_limit.py:14`（`MAX_RULE_LINES = 10`）/ `scripts/lib/discover/suppression.py:12,178-180` / 2026-08-16 |
| P8 | 朝の y/n の1日提示上限は 5 件（`None` で全件）。**選択肢を増やしても設問数は増えない** | [コード] | `scripts/lib/correction_semantic/daily_review.py:374`（`max_groups: Optional[int] = 5`）/ 2026-08-16 |
| P9 | y/n の結果は `record_reviewed(decision=...)` で既読ストアへ記録される。`decision` は自由文字列（現状 `"promoted"` / `"rejected"`） | [コード] | `daily_review.py:111-118,145-152` / 2026-08-16 |

### 2.2 「記録のみ」の実態に関する前提

| # | 前提 | 等級 | evidence |
|---|---|---|---|
| P10 | `idiom_autopromote()` は凍結中で **no-op**。`is_frozen()` が先頭で `promoted:0, frozen:True` を返し、照合ロジックに到達しない | [コード] | `scripts/lib/correction_semantic/idiom_autopromote.py:92-99`（`if shrink_freeze.is_frozen(): return {...,"frozen": True}`）+ `shrink_freeze.py:44`（`SHRINK_FREEZE_ACTIVE: bool = True`）/ 2026-08-16 |
| P11 | promote 済み（`applied`）は reflect 以外にも **2つの改善レーンから消える**: 直接パッチ最適化と episodic 照合 | [コード] | `skills/genetic-prompt-optimizer/scripts/optimize_core.py:79`（`if record.get("reflect_status") == "applied": continue`）/ `scripts/lib/episodic_retriever.py:80`（pending レコード前提）/ 2026-08-16 |
| P12 | `applied` は **90 日で prune 削除の対象**になる（`pending` は常に保持） | [コード] | `scripts/lib/prune/corrections.py:84-107`（`status not in ("applied","skipped")` は保持 → `age_days > decay_days` で削除）+ `scripts/lib/prune/config.py:9`（`DEFAULT_DECAY_DAYS = 90`）/ 2026-08-16 |
| P13 | 「記録のみ」の活用先は issue 記載の3つが**全部ではない**（下記 §3.3 に7つ列挙）。ただし **どれも AI の振る舞いを変えない**という結論は変わらない | [コード] | §3.3 の表（各行に file:line）/ 2026-08-16 |

### 2.3 取り消し（revert）に関する前提

| # | 前提 | 等級 | evidence |
|---|---|---|---|
| P14 | revert の対象パス解決は **skills root しか知らない**。`scope="global"` は `~/.claude/skills` に解決されるので、`~/.claude/rules/*.md` は原理的に到達不能 | [コード] | `scripts/lib/evolve_revert/_target.py:57-66`（`if scope == "global": root = global_skills_root()`）+ `scripts/lib/evolve_decision_ids.py:341-344`（`Path.home()/".claude"/"skills"`）。root 外は `REASON_ESCAPES_ROOT` / 不在は `REASON_NOT_FOUND` / 2026-08-16 |
| P15 | 現行 revert は **既存ファイルの書き換えのみ**表現できる（before 本文が必須。「不在」sentinel が無い） | [コード] | `scripts/lib/evolve_revert/_availability.py:41,66`（`_SUPPORTED_SCOPES = ("global","project")` / `revert_before_b64` 必須）。理由ラベルも「この種類の採用（rules / hooks 等）は戻す機能の対象外です」と明記 `_availability.py:50-53` / 2026-08-16 |
| P16 | 採用履歴ストア `optimize_history` は `store_registry` に**宣言が無く**、`append_entry` は `store_write` barrier を通らず直接 `open(...,"a")` する | [コード] | `scripts/lib/optimize_history_store.py:31,200-213`。`store_registry.py` 内の `optimize_history` 出現は :342 の説明文のみ / 2026-08-16 |

### 2.4 実測（このマシンの実ストア・他環境では再現しない）

| # | 前提 | 等級 | evidence（値 / コマンド / 取得日） |
|---|---|---|---|
| P17 | corrections は全 **177 件**。`reflect_status` は `applied 168 / skipped 8 / pending 1`。`source` は `reflect_confirmed 167 / backfill 8 / hook 2`。**`reflect_confirmed` は 167 件すべてが `applied`** | [実測] | M1 / 2026-08-17（M0 指紋 lines=177） |
| P18 | `reflect_confirmed` の日付分布は 2026-06-12 の 41 件が最多。最古 **65 日前**・最新 0 日前。**90 日超は現在 0 件**（＝まだ1件も prune されていない） | [実測] | M2 / 2026-08-17 |
| P19 | 2026-08-15 に朝の y/n で promote されたのは **3 件**。内容は ①「並行して作業できないの？」②「spec-keeper は codex レビューいらない」③「memory だとこの PJ だけじゃないの？ rule にした方がよくない？」。3件とも `routing_hint: None` / `extracted_learning: None` | [実測] | M1 の変形（`timestamp[:10] == "2026-08-15"` で絞る）/ 2026-08-16 取得（2026-08-17 再取得でも同一 3 件・値は日付範囲固定のため store 増加の影響を受けない） |
| P20 | fleet 全体の採用履歴は **42 件**、うち **戻せるもの（`revert_schema_version` あり）は 1 件だけ**（`skills/spec-keeper/SKILL.md`・scope=project・2026-08-15） | [実測] | M3 / 2026-08-17（2026-08-16 時点から件数不変） |

### 2.5 測定不能（空欄にしない）

**この表は rev1 時点のスナップショット。U1・U2 はその後解決/決定済み — 最新状態は §11 を参照**
（この表を毎回更新すると二重管理になるため、ここでは初出時点の理由だけを残す）。

| # | 事項 | 理由（rev1 時点） |
|---|---|---|
| U1 | `AskUserQuestion` の選択肢上限が 2〜4 であること | **測定不能（本セッション）**。当該 tool が本セッションに提供されておらず、実行して境界を確かめられない。issue #475 コメント（2026-08-15 ユーザー）の主張をそのまま前提に採る。→ **§11 で確定（2〜4個 + Other 自動付与）** |
| U2 | 「Other」（自由記述）から残り反映先へ確実に到達できること | **測定不能（本セッション）**。U1 と同じ理由。→ **§11・§12 で決定（自由記述で受け、判断が付かない場合は黙って対象外にせずその場で1回聞き返す）** |
| U3 | ルール文書に書いた指摘が実際に AI の振る舞いを変えたか | **測定不能（現時点）**。因果判定 (b) は revert 済みを畳んだ有効 accept 件数が分母で、`bin/evolve-audit --growth` が `not_measured` を返す段階（ADR-054 §5・§7.2）。**本設計はこれを解決しない**（届くようにするだけ） |

---

### 2.6 codex cold review 後の追加実測（2026-08-16・P21〜P25）

codex が「測れるのに測っていない」と指摘した前提を実測した結果。**うち2件は設計を変えた。**

| # | 前提 | 等級 | evidence（値 / 取得元 / 取得日） |
|---|---|---|---|
| P21 | **skill レーンに correction 単位の入力口は存在しない** | [コード]（codex cold review 2巡目 [Must]6 で訂正。実ストアの観測ではなく、生成箇所・関数シグネチャ・未接続 baseline を読んだ構造証明のため） | 朝の y/n が読むのは `matched_skills` と `skill_evolve` の2種のみ（`scripts/lib/evolve_decisions/_candidates.py:97,109`）。`skill_evolve/proposal.py:230` の `evolve_skill_proposal(skill_name, skill_dir)` は「既存スキルに自己進化テンプレを入れるか」の判定用で、correction を渡す引数を持たない / 2026-08-16 |
| P22 | **`hook_candidates` は読み手ゼロ。未接続として公式登録済み** | [コード]（同上。理由は P21 と同じ） | 生成は `scripts/lib/discover/runner.py:372-374`、未接続登録は `scripts/lib/fixtures/proposal_lane_unconnected_baseline.txt:4` / 2026-08-16 |
| P23 | **`suggest_claude_file` は 167 件中 161 件（96.4%）が `None`。返り値は候補1つ（リストでない）。`confidence` は6件とも `0.85`**（当初「confidence 全件 0.9 固定」は誤り。0.9 は correction レコードの**入力側**固定値であり、`suggest_claude_file` が**返す**マッチ confidence とは別物 — 詳細は本表下の訂正記録） | [実測] | `reflect_confirmed` 167 件・`project_root=cwd` 固定の dry-run（LLM 不使用・読み取りのみ）。**再現コマンドは §1.1 M5** / 2026-08-17（M0 指紋 lines=177） |
| P24 | **`reflect_data_count`（`/reflect` 提案の駆動値）は `pending` のみを数える。閾値は 5 件で AskUserQuestion による提案が MUST** | [コード] | `discover/suppression.py:183-196` → `discover/runner.py:218-219` → `skills/evolve/references/correction-review.md:11-16` / 2026-08-16 |
| P25 | **revert の conflict 判定はファイル全体の SHA256 比較。行単位ではない** | [コード] | `scripts/lib/evolve_revert/_apply.py:294-301` / 2026-08-16 |

**P23 の訂正記録（codex cold review 2巡目 [Must]4）**: 初版は「`confidence` は全件 0.9 固定」と
書いていたが、これは**correction レコード自身が持つ入力側の `confidence` フィールド**
（`correction_semantic/promote.py:367` が昇格時に書き込む固定値 `0.9`）と、
`suggest_claude_file` が返すマッチ結果の confidence（`scripts/lib/reflect_routing.py` の
分岐ごとに 0.90 / 0.88 / 0.85 / 0.80 / 0.75 / 0.60 のいずれか）を**混同していた**。
2026-08-17 に §1.1 M5 のコマンドで再実行した結果、167 件中 `None` にならなかった 6 件は
**すべて `confidence: 0.85`**（`.claude/rules/project-specific.md` へのプロジェクト固有シグナル
一致3件 + `~/.claude/CLAUDE.md` への `always/never/prefer` 一致3件）だった。この訂正は
**「並べ替えという機能が実質存在しない」という結論には影響しない**（96.4% が `None`・返り値は
候補1つのみという事実は変わらない）。

---

## 3. issue #475 の主張の裏取り

### 3.1 裏付いた（3つの根拠すべて再現）

issue 本文の3行の表は **そのまま正しい**（P1 / P3 / P5）。「promote した時点で処理済みと見なされ、
反映を担う工程から不可視になる」は [コード] で確定する。データの偏りではなく**分岐の帰結**なので、
他環境でも同じことが起きる。

「2026-08-15 に3件 promote したが、いずれもルール文書には1行も書かれていない」も裏付いた。

### 3.2 「0 件」の根拠（何をもって 0 と言うか）

**構造による証明を主にする**（件数の数え上げは補助）。

1. [コード] 朝の y/n から rule ファイルへ至る書込み経路は**存在しない**。ルール文書を書くのは
   agent の Edit/Write であり、その手順は reflect スキルの Step 6（`skills/reflect/SKILL.md:125`）にしかない。
   Step 6 の入力は pending レコードのみ（P3）。promote は applied を付ける（P1）。
   → **promote した瞬間に Step 6 の入力集合から外れる。したがって到達件数は 0 でしか有り得ない。**
2. [コード] さらに `suggest_claude_file` はグローバル rule を候補に持たない（P6）。
   仮に pending のまま Step 6 に載っても、「グローバル rule に書く」提案自体が出ない。
3. [実測] 補助証拠として、`~/.claude/rules/` に 2026-08-15 promote の3件に対応する記述は無い。
   同日更新の `explain-clearly.md`（10:30）は別のユーザー直接指示（「説明する文章は社長に説明する文章にして」）が
   出所で、promote 3 件のいずれとも内容が一致しない。②は `MEMORY.md` の
   `feedback_no_codex_review_for_spec_keeper.md`（2026-08-10・promote より前）に存在するが、
   これは promote の結果ではない。

> **注記**: `~/.claude` は git 管理下にないため（`git -C ~/.claude rev-parse` が `not a git repository`）、
> 「いつ誰が書いたか」の履歴では証明できない。だから 1. の構造証明を主にしている。

### 3.3 一部違った: 「記録のみ」の活用先は3つではなく7つ（ただし結論は変わらない）

issue は活用先を3つ（PJ 横断の優先提示 / 棚卸しの数字 / 過汎用 idiom の除外判断）としているが、
実際に promote 済みレコードを読む場所を全部数えると **7 つ**ある。**すべて計測・表示・保守用で、
AI の振る舞いを変えるものは1つも無い**ので issue の結論は正しい。

| # | 読み手 | 何に使うか | file:line | 等級 |
|---|---|---|---|---|
| 1 | `cross_pj_priority` | 他 PJ で承認済みの提示（自動承認しない） | `scripts/lib/correction_semantic/cross_pj_priority.py:9-18` | [コード] |
| 2 | `growth_report.count_promoted_today` | 棚卸しの数字（本日累計） | `scripts/lib/growth_report.py:67-79` | [コード] |
| 3 | `idiom_filter` | 過汎用 idiom の除外判断（FP guard） | `scripts/lib/correction_semantic/idiom_filter.py:3-9` | [コード] |
| 4 | `audit/memory.py` | memory エントリが「反映済み correction 由来」かの provenance 突合 | `scripts/lib/audit/memory.py:482` | [コード] |
| 5 | `audit/memory_contagion.py` | human 由来 vs 機械由来の比率（評価源バイアス検出） | `scripts/lib/audit/memory_contagion.py:10` | [コード] |
| 6 | `issues_summary` | 「未処理」件数の分母（applied を除外） | `scripts/lib/issues_summary.py:35-42` | [コード] |
| 7 | `prune/corrections` | **90 日超で削除**（活用ではなく破棄） | `scripts/lib/prune/corrections.py:84-107` | [コード] |

### 3.4 issue が書いていない、より悪い事実が2つある

**(a) promote は3つのレーンから同時に消す。** reflect（P3）だけでなく、
直接パッチ最適化（`optimize_core.py:79` が `applied` を除外）と episodic 照合（P11）からも消える。
「はい」を押すほど**改善に使える素材が減る**という issue タイトルの構図は、reflect 単独より広い。

**(b) 「記録」は恒久ではない。90 日で消える。** [コード] P12 + [実測] P18。
現在 90 日超は 0 件だが、最古が 65 日なので **2026-09-10 前後に 2026-06-12 分の 41 件が
prune 対象に入る**（`evolve` の Phase 4 で `cleanup_corrections` が走る）。
issue の必須要件 1b が指す「2026-08-15 にユーザーが誤解した」説明（「恒久ルールとして残す」）は、
**保留であることだけでなく永続性の点でも事実と違っていた**。選択肢の文言はこの両方を直す必要がある。

---

## 4. 何を変えるか — 設問を1回にして反映先まで決める

### 4.1 変更の範囲（1箇所だけ）

**変えるのは `skills/evolve/references/correction-review.md` の Step 6.2 の AskUserQuestion テンプレ**
（現在は `はい（昇格）` / `いいえ（却下）` / Skip の3分岐。`correction-review.md:65-68`、
要約は `skills/evolve/SKILL.md:255`）。ここを反映先つきの1問に差し替える。

**2段階では聞かない**（issue 必須要件1）。設問数は据え置き（1 group = 1 問・最大5問）。P8 より、
選択肢が増えても朝の設問数は増えない。

### 4.2 選択肢の並べ方（反映先を4種に絞って1問に収める）

反映先は issue コメントで **6 種**（グローバル rule / PJ rule / skill / hook / memory / pitfall）が
挙がったが、**初期スコープは rule 2 種 + memory + pitfall の 4 種に絞る。skill と hook は外す**
（tacchi レビュー Must-4・§14）。

| 外す反映先 | 理由 |
|---|---|
| **skill** | 既に `skill_evolve` レーンが独自の y/n と採用履歴（`optimize_history`）を持つ。ここから直接書くと同じ採用が2レーンに分かれて記録され、**`bin/evolve-revert --list` に出ない skill 変更**が生まれる（revert 迂回） |
| **hook** | P15 のとおり revert 経路が存在しない。**戻せない反映**を朝の30秒の中で作らせるのは柱4（1コマンドで戻せる）と正面から衝突する。hook 提案は #467 の未接続13種側で扱う |

**「既存レーンへ回す」とは書かない**（当初案を撤回）。T2 実測で、回す先の**入力口が実在しない**ことが
確定したため（§2.6-P21/P22）。skill / hook を Other で書かれた場合は
**「この設問の対象外です（別途 `/evolve-anything:evolve` の skill 提案で扱います）」と正直に表示して終わる**。
「回しました」と言わない。

**選択肢は固定にする（機械提案による並べ替えはしない）。**

当初案は `suggest_claude_file`（`scripts/lib/reflect_routing.py:108`）で反映先候補を並べ替える
方式だったが、**実測でこの前提が崩れた**（T1・§2.6-P23）:

| 実測結果（`reflect_confirmed` 167件・2026-08-17・再現コマンドは §1.1 M5） | 値 |
|---|---|
| 返り値が `None`（どの分岐にも当たらない） | **161 件（96.4%）** |
| `~/.claude/CLAUDE.md` | 3 件 |
| `.claude/rules/project-specific.md` | 3 件 |
| `~/.claude/rules/*.md`（グローバル rule） | **0 件**（P6 を再確認） |

さらに同関数の返り値は `Optional[Tuple[path, confidence]]` ＝ **候補を1つしか返さない**。
リストを返さないので、**「上位2件を並べる」という設計自体が実装上成立しない。**
`confidence` は非 `None` の6件すべてが `0.85` だった（`<0.75` の分岐＝auto-memory フォールバックは
実データで一度も発火しない）。**当初「confidence 全件 0.9 固定」としていたのは誤り**（0.9 は
correction レコード入力側の固定値と、関数が返すマッチ confidence を混同していた。詳細は §2.6 P23 直下の訂正記録）。

**採る方式（固定4択）**:

| 位置 | 内容 |
|---|---|
| 1 | 共通ルールに書く（全PJで効く） |
| 2 | このPJのルールに書く |
| 3 | いまは反映しない（記録は残す） |
| 4 | いいえ（この指摘は不要） |
| Other | tool が自動付与する自由記述欄。memory / pitfall はここに書く。skill / hook を書かれた場合の応答は §4.4 |

順番を機械に決めさせないので、**毎朝同じ位置に同じ選択肢が出る**。並べ替えの精度（旧 U4）は
**そもそも並べ替えをしないので論点ごと消える**。**「位置固定が朝の体感時間を縮める」という
効果自体は未検証**（codex 3巡目 [Must]B-1・§11 U9）。設計判断としての「固定にする」根拠は
並べ替えが実装上成立しないこと（上記）で十分であり、体感時間短縮効果を前提にしない。

### 4.3 選択肢の文言（実際に出す日本語）

question 本文（**起草した1行を先に見せる**・tacchi Must-6）:

```
「{idiom または representative}」（{count}回・{他PJ承認済みなら「他PJ（slug…）で承認済み」}）

書く文面（案）: {draft_line}

この指摘を、どこに反映しますか？
メモや落とし穴集に残したい場合は Other に記入してください。
```

**`{draft_line}` は誰が作るか**: **agent が AskUserQuestion を呼ぶ前に起草する**。
§5 の不変条件どおり書くのは常に agent の Edit/Write なので、agent はどのみちこの1行を作る。
やることは**作る順番を「選ばせた後」から「選ばせる前」に移すだけ**で、新しい生成器も
LLM 呼び出しの追加も要らない。反映先によって文面が変わる場合は、上位候補（選択肢1）の
文面を出し、他を選んだら書く直前にもう一度確定文面を出す。

これが無いと「何が書かれるか分からないまま全PJに効くルールを承認する」ことになる
（tacchi の指摘: 承認の中身が空になる）。

options（label / detail をそのまま出す。**tacchi 2巡目 Must-1・Must-2 を受けて全面書き換え** —
利用者の画面に § 番号・file:line・内部語（「回しません」等）を出さない。理由は下記）:

| # | label | detail（必ず添える） |
|---|---|---|
| 1 | **共通ルールに書く（全PJで効く）** | 次のセッションから全プロジェクトで効きます。あとで1コマンドで取り消せます（条件は反映時に表示）。 |
| 2 | **このPJのルールに書く** | 次のセッションからこのプロジェクトだけで効きます。取り消しも同様です。 |
| 3 | **いまは反映しない（記録は残す）** | 動作は変わりません。記録は消えず、5件たまったら見直しをまとめて案内します。 |
| 4 | **いいえ（この指摘は不要）** | 記録も反映もしません。次回から出しません。 |

**Other 行を上表に含めない（tacchi 2巡目 Must-2）**: `AskUserQuestion` は `options` の他に
**Other を自動付与**し、利用者はカスタム detail 抜きの「Other」という素の選択肢しか選べない
（U1 で確定済みの tool 入力スキーマの制約 — `options` は独自定義した2〜4個のみで、Other は
tool 側が付与するプレーンな自由記述欄）。**§4.3 の初版は Other 行に detail 文言を書いていたが、
これは設問時点でどこにも表示されない空文だった**（自分自身の U1 実測と矛盾していた）。

memory / pitfall への誘導は detail ではなく、**question 本文の末尾に1行**として出す
（上の question テンプレの最終行）。skill / hook を書かれた場合の応答は
**選ばせる前ではなく、Other に自由記述された後の応答**として §4.4 に定義する（下記）。

**上記 detail 文言は §11-A の実測を受けて簡潔化・再測定済み**（1問あたり平均357字・最大377字で
400字以内に収まることを確認した。tacchi Must-1 が要求する情報量 — 反映範囲・取り消し可否・
90日削除されない旨 — は保ったまま、§番号・冗長な言い回し・詳細な取り消し条件を削った。
取り消し条件の詳細は §4.3.1 のとおり反映完了メッセージへ移した）。

### 4.3.1 「あとで戻せます」の詳細条件はいつ・どこで見せるか（tacchi 2巡目 Must-4）

選択時点（上表の detail）では書き込み先ファイルがまだ確定していないため、「同じファイルを
その後に変更していない場合」という条件を**選択時点で見せても利用者は評価できない**
（「同ファイルってどのファイル？」で止まる）。**精密な条件は選択時点ではなく、実際に反映が
完了した直後のメッセージと `bin/evolve-revert --list` に出す**:

```
反映しました: ~/.claude/rules/xxx.md（1行追記）
取り消す場合: bin/evolve-revert <entry_id>
※このファイルをこの後さらに変更すると、この取り消しはできなくなります
```

選択時点の detail は「取り消せます（条件は反映時に表示）」とだけ言えば足りる
（条件が存在すること自体は予告しているので過剰約束にならない）。

### 4.3.2 新規ファイル判明時の追加確認は3択にする（codex cold review 2巡目 [Must]9 + tacchi 2巡目 [Should]C-1）

選択時点（AskUserQuestion の回答時点）では、選んだ scope（共通/PJ）の中の**どのファイルに書くか**
がまだ決まっていない（P5・§9 のとおり agent が Edit 前に判断する）ため、
「既存ファイルへの追記か・新規ファイル作成か」もこの時点では未確定。よって:

1. ユーザーは1・2を選ぶ時点では「取り消せます（条件は反映時に表示）」とだけ伝えられる（上表の detail）。
2. agent が対象ファイルを決定した**直後・実際に Edit/Write する前**に、新規作成と判明した場合のみ
   追加で1問を出す。**tacchi 2巡目 Should-1 を受けて y/n から3択に変更**（「戻せません。続けますか？」
   では拒否の逃げ道しか無く、利用者が「じゃあどうすればいいの」で止まる）:

   | # | label | detail |
   |---|---|---|
   | 1 | 新しく作る | このファイルは無いので新規作成します。**取り消せません**。 |
   | 2 | **既存の `<候補ファイル>` に追記する（推奨）** | 取り消せます。テーマが近い既存ファイルに1行追記します |
   | 3 | やめる（記録だけ残す） | 反映しません。§4.3 の選択肢3と同じ扱いになります |

   `<候補ファイル>` は agent が既存の書き込み規約（P5・§9）と同じ判断で提案する
   （テーマの近い既存ファイルを1つ選ぶ）。**グローバル rules は既に33ファイルあり
   （§8.3）、適切な既存ファイルが見つかることが多いため、選択肢2を既定候補として提示する。**
3. 既存ファイルへの追記（1・2 いずれで判定されても実質「追記」に落ち着くケースを含む）なら、
   この追加確認はスキップしてそのまま書く。
4. 朝の設問数（P8・1 group = 1 問・最大5問）は変わらない。この追加確認は**稀なケース**
   （§8.3: グローバル rules は既に 33 ファイルあり新規作成が必要な場面は多くない）に限った
   例外的な2問目であり、通常フローの設問数には数えない**が、発生頻度自体は未観測**
   （tacchi 2巡目 [Nit] — §11 に観測項目として追記した）。

**1b への対応（保留であることが読み取れる文言）**: 3 の label から「記録のみ」という中立語を外し、
**「いまは反映しない」**を先頭に置いた。detail の1文目を「動作は変わりません」にして、
効果がないことを最初に読ませる。§12 決定2 で「90日で消さない」が確定したため、
**「記録は消えず」と正直に書く**（削除される事実を隠すのではなく、削除されないことをそのまま書く。
§3.4(b) が指摘した2026-08-15 の「恒久ルールとして残す」という当時の誤った説明は、
削除しない決定が確定した現在は**そのまま正しい**説明になった）。

**4つとも位置固定**（tacchi Must-5 + T1）。指摘の内容にかかわらず同じ順番で出す。
却下手段（4）が消えると不要な指摘が毎朝出続け、承認疲れが1日5件の上限（P8）を超える。
§12 の決定1 のとおり、ユーザー判断からは外している。

### 4.4 Other に自由記述された後の応答（tacchi 2巡目 Must-2）

Other は tool 側が自動付与する素の自由記述欄なので、選択**前**に skill / hook 向けの案内を
出すことはできない（上記のとおり）。したがって skill / hook の扱いは、Other に**書かれた後**の
応答として定義する。応答は自由記述の内容で分岐する（既存の分類・§5 の行を参照）:

| 自由記述の内容 | 応答 | 実装 |
|---|---|---|
| memory 関連 | 既存の memory 反映フロー（§5「memory（Other）」行）へ | 変更なし |
| pitfall 関連 | 既存の pitfall-curate フロー（§5「pitfall（Other）」行）へ | 変更なし |
| **skill 関連** | 「この場では反映されません。skill を直す場合は `/evolve-anything:evolve-skill <名前>` を実行してください」 | **具体的な次の一手を示す**（旧案の「既存の採用フローへ回します」は P21 の実測どおり受け渡し口が無いため嘘だった。回すとは言わず、実行可能なコマンドを案内する） |
| **hook 関連** | 「hook への反映は自動化されていません。必要なら `hooks/` を手で編集してください」 | 同上（P22: `hook_candidates` は読み手ゼロで未接続登録済みのため、回す先自体が存在しない） |
| 判断が付かない | 聞き返す（§11 U2 の決定どおり） | 変更なし |

**「回します」と言わない**のは §4.2 T2 の結論（受け渡し口が実在しない）のままだが、
「対象外です」で終わらせず**利用可能な次の一手**を示す点が tacchi 2巡目の追加要求。

### 4.5 設問を出す前の重複チェック（tacchi 2巡目 [Should]C-2）

**当初案は毎回 §4.3 の設問を出す前提だったが、既に反映済みの指摘まで再度聞くのは無駄**
（同じ内容の指摘が複数回 promote されることは P19 でも起きている）。§6.1 の実在確認
（正規化後の完全一致判定・§6.2）を**設問を出す前にも流用する**:

1. `draft_line` を起草した後、AskUserQuestion を呼ぶ**前**に、agent が候補の反映先ファイル
   （P5・§9 の既存判断ロジックで推定する候補）を対象に §6.2 の正規化一致判定を実行する。
2. 一致が見つかれば、**設問枠を消費せずに**「この指摘は既に `<ファイル>` に反映済みでした」と
   1行報告し、`reflect_status` を直接 `applied` に更新する（§6.1 の CLI ゲートを同じ経路で通す。
   新規実装はほぼゼロ — 既存の確認関数を「書いた後」ではなく「書く前」に呼ぶだけ）。
3. 一致が無ければ、通常どおり §4.3 の設問を出す。

**効果**: 重複指摘が保留キューに滞留し、5件たまるたびに `/reflect` へ回されて再提示される
（§5.1）という承認疲れの種を、そもそも設問に出る前に摘む。

### 4.6 過去に「はい」した記録の移行（tacchi 2巡目 [Should]C-3）

**当初案には移行に関する記述が1行も無かった。** 2026-08-17 時点で `reflect_confirmed` は
**167件全てが `applied`**（P17）だが、P4 の構造証明が示すとおり**この167件は1行も反映されて
いない**（promote した時点で反映を担う工程から構造的に不可視になるため、対象は P19 の3件に
限らず167件全件）。本設計は**未来の指摘だけを直しており**、この事実を利用者に伝える文言が無い。

**採る案（新しい仕組みは作らない。§5.1 の既存メカニズムに乗せる）**:

1. 実装時に**1回限りの移行バッチ**を実行し、`reflect_confirmed` かつ `applied` の全件
   （実装時点の件数。2026-08-17 時点なら167件）の `reflect_status` を `promoted` に一括変更する
   （§6 の値域変更後に実施。`corrections.jsonl` への直接書き換え・1回限り）。
2. これにより §5.1 の既存メカニズム（`reflect_data_count` が `pending`+`promoted` 合算で
   5件以上 → evolve が `/reflect` 実行を提案）が自動的に発火する。**新しい通知経路は作らない** —
   167件は `/reflect` のバッチレビュー（Step 6 の対話。1件ずつ承認・編集・却下できる）で
   ユーザーが順次確認できる。朝の1日5問の枠（P8）は消費しない（`/reflect` は別セッション）。
3. **初回だけの通知**: 移行バッチ実行時に「これまでの『はい』167件は反映されていませんでした。
   `/reflect` で順次確認できます」の1行を出す（migration marker で1回のみ発火。仕組みは
   `learning_derive_state_from_logs_not_forward_write` と同型 — 新しいストアは作らず、
   移行実行の有無を `reflect_status` の分布から read 時に判定できる）。
4. P19 の3件（2026-08-15分）は167件に含まれるため、**個別の特別対応は不要**
   （一括移行でカバーされる）。

**壊して赤くする検査**: 移行バッチを実行しないまま実装した場合、167件は `applied` のまま
`reflect_data_count` にも入らず（`("pending","promoted")` にしか入らないため）、
`/reflect` 提案も発火しない＝**この移行節が無いと過去分は永久に不可視のまま**（F6 と同型の
取りこぼし）。§10 の失敗モード表に F13 として追記済み。

---

## 5. 選んだ後に何が起きるか（反映先ごと）

**共通の不変条件**: 書くのは常に **agent の Edit/Write**（P5）。python の新規 writer は作らない。
`--dry-run` では一切書かない（既存の dry-run ゲート貫通規約と同じ）。

| 選択 | どのファイルに | どういう形式で | 誰が書くか | 承認 |
|---|---|---|---|---|
| 共通ルール | `~/.claude/rules/<既存 or 新規>.md` | 既存ファイルなら**末尾に1行追記**。10行上限（P7）を超えるなら `check_line_limit` の警告を出して分離を提案 | agent（Edit / 新規なら Write） | この y/n 自体が承認 |
| PJ ルール | `<repo>/.claude/rules/<既存 or 新規>.md` | 同上 | agent | 同上 |
| いまは反映しない | `corrections.jsonl` | 現状と同じ1行（ただし `reflect_status` は §6 の新値 `promoted`） | 既存 `promote_signals` | 同上 |
| いいえ | `correction_review_seen.jsonl` | `record_reviewed(..., decision="rejected")`（既存・P9） | 既存 CLI | 同上 |
| memory（Other） | `~/.claude/projects/<enc>/memory/` | `auto_memory_broker` の既存経路（project スコープ4層防御 + `memory_guard`） | 既存経路 | 同上 |
| pitfall（Other） | `references/pitfalls.md` | **手で markdown を編集しない**。`pitfall-curate` 経由（#471） | `pitfall-curate` | 同上 |
| skill（Other） | — | **この場では書かない**。「`/evolve-anything:evolve-skill <名前>` を実行してください」と次の一手を案内（§4.4・tacchi 2巡目 Must-2）。T2 実測で受け渡し口が実在しないと確定（P21） | — | — |
| hook（Other） | — | **この場では書かない**。「`hooks/` を手で編集してください」と次の一手を案内（§4.4）。`hook_candidates` は読み手が公式にゼロ（P22） | — | — |

### 5.1 「いまは反映しない」を選んだ指摘が reflect に再浮上する経路（tacchi Must-3）

**当初案には穴があった。** 素案は「`record_reviewed` を呼ばない（既読にしない）ので次の朝もう一度出る」
としていたが、これは2つの点で成立しない:

1. **同じ correction 行が毎朝重複追加される**。`promote` は毎回 `corrections.jsonl` に append するため、
   既読にしないまま再提示すると行が増え続ける
2. **`promoted` は reflect の入力集合に入らない**。`reflect.py:127-131` と `:1006` は
   `reflect_status == "pending"` だけを拾う。§6 で `promoted` を新設すると、
   **「いまは反映しない」を選んだ瞬間にどちらのレーンからも消える**（朝からも reflect からも）

**採る案（既存の条件式を広げるだけ・新設ゼロ）**:

| 変更点 | file:line | 変更内容 |
|---|---|---|
| 朝のレーン | `daily_review.py:111`（`record_reviewed`） | **既読にする**（`decision="deferred"`）。毎朝の重複提示と重複 append を止める |
| reflect の入力集合 | `reflect.py:127-131`, `:1006` | `== "pending"` → `in ("pending", "promoted")`。**保留は reflect で拾う** |
| `--skip-all` | `reflect.py:1006-1009` | 上記で `promoted` も一括 skip の対象に入る。**入れる**（保留を明示的に畳めるのは正しい） |
| **evolve の `/reflect` 提案** | `discover/suppression.py:183-196`（`load_claude_reflect_data`） | **変える（当初「変えない」としたのは誤り）**。`== "pending"` → `in ("pending","promoted")`。T3 実測: この件数は `discover/runner.py:218-219` の `reflect_data_count` に入り、**5件以上で `/reflect` 実行を提案する MUST 分岐**（`skills/evolve/references/correction-review.md:11-16`）を駆動している。ここを直さないと**保留が何件たまっても evolve は `/reflect` を勧めない**＝P3/P4 の穴が形を変えて再発する |

**帰結**: 「いまは反映しない」は「朝の設問には再提示されないが、reflect には残り、
**5件たまったら evolve が自動で `/reflect` を勧める**」状態になる。
§4.3 の文言 3 は「あとで反映したくなったら、朝の確認でもう一度出せます」ではなく
**「あとで見直しの確認でまとめて出せます（5件たまると自動で案内します）」**が正しい。文言を差し替える。

**壊して赤くする検査（2方向）**: ①`reflect.py` の条件を `== "pending"` に戻したら赤
（保留が拾われないことを検出）／②`daily_review` の `record_reviewed` 呼び出しを外したら赤
（同一 signal_key が2回提示されうることを検出）。

---

## 6. `reflect_status` の意味を分ける（issue 必須要件3・受入条件4）

現状は「昇格済み（人間が"はい"と言った）」と「反映済み（文書に書かれた）」がどちらも `applied`。
この設計を入れても両者を区別しないと、**同じ取りこぼしが別の形で再発する**（issue 必須要件3）。

**採る案**: `reflect_status` の値域を3つにする。**新しいフィールドもストアも増やさない。**

| 値 | 意味 | 誰が付けるか |
|---|---|---|
| `pending` | 未判断 | hook（`hooks/correction_detect.py:144`） |
| `promoted` | **昇格済み・反映先は未定**（＝「いまは反映しない」を選んだ） | `promote.py` |
| `applied` | **反映済み**（反映先ファイルに該当行が実在することを CLI が確認した） | `promote.py` / reflect の既存更新経路。ただし**§6.1 の実在確認を通過した場合のみ** |

**既存 reader への影響（全件洗い出し済み・[コード]）**:

| reader | 現在の条件 | `promoted` 追加後の挙動 | 評価 |
|---|---|---|---|
| `discover/suppression.py:196` | `== "pending"` | 拾わない（現状と同じ） | 変化なし |
| `reflect.py:127-131` | `== "pending"` | 拾わない（現状と同じ） | 変化なし |
| `optimize_core.py:79` | `== "applied"` を除外 | **拾うようになる** | 望ましい（§3.4(a) の穴が1つ塞がる） |
| `prune/corrections.py:84` | `("applied","skipped")` のみ削除 | **削除されなくなる** | 望ましいが要判断（§12-2） |
| `issues_summary.py:42` | `!= "applied"` を未処理 | **未処理に数えられる** | 望ましい（保留は未処理） |
| `audit/memory.py:482` | `== "applied"` | 拾わない | 変化なし（反映済みだけを provenance にするのは正しい） |
| `memory_contagion.py` | `source` を見る（status 非依存） | 変化なし | 変化なし |
| `episodic_retriever.py:80` | 呼び出し側が pending を渡す | 変化なし | 変化なし |

**tacchi Must-2 を受けて再洗い出し（`grep -rn reflect_status scripts/ hooks/ bin/ skills/`・tests 除く）。
上の8件に加えて漏れが3件あった**（2026-08-16 実測）:

| reader | 現在の条件 | `promoted` 追加後の挙動 | 評価 |
|---|---|---|---|
| `reflect.py:327` | `== "applied"` を類似度クラスタ化し**再発回数**を数える（#184 の memory 候補判定） | `promoted` は数えない | **正しい**（保留は「反映済みの再発」ではない）。ただし §6.1 導入後は `applied` の意味が「実在確認済み」に狭まるため、**この件数は今より小さく出る**。これは測定の改善であって退行ではない旨を実装 PR に明記する |
| `reflect.py:1006-1009`（`--skip-all`） | `== "pending"` のみ一括 `skipped` 化 | §5.1 で `promoted` も対象に含める | **意図的な変更**（保留を明示的に畳める） |
| `skills/reflect/SKILL.md:83, 151-154` | 手順書が `applied` を書けと直接指示している（`approve` / `edit` / `false-positive` / `skip`） | **文書側を §6.1 に合わせて書き換えないと、agent が実在確認を飛ばして `applied` を書く** | **要修正**。`learning_skill_md_must_not_enforcement` の既知パターンそのものなので、文書の書き換えだけに頼らず §6.1 の CLI 側ゲートで担保する |

writer 側（`pending` を固定で書くだけ・影響なし）: `hooks/correction_detect.py:144` /
`scripts/migrate_reflect_queue.py:53` / `scripts/backfill_preceding_tool_calls.py:218`。

---

### 6.1 `applied` は「反映先に該当行が実在する」ことを確認してから付ける（tacchi Must-1）

tacchi の指摘の核心:

> 4択化そのものは正しいが、病巣は「**選んだ＝反映された、を誰も検証しない**」こと。
> これが無ければ選択肢を増やしても同じ取りこぼしが再発する。

実際、現状の `applied` は「人間が"はい"と言った」だけで付く。**書き込みが失敗しても、
agent が手順を飛ばしても、`applied` は付く**。§6 で状態を3つに分けても、`applied` の付与条件が
「人間の同意」のままなら、名前が変わるだけで同じ嘘が残る。

**採る案**: `applied` を書く直前に、**反映先ファイルに該当行が実在するかを決定論で確認する**。
確認できなければ `applied` を書かず `promoted` のまま残し、警告を出す。

| 項目 | 内容 |
|---|---|
| 実装場所 | `reflect.py:470` の `update_reflect_status` に確認を内蔵する（呼び出し側に任せない。**新規 CLI サブコマンドは作らない**＝`feedback_evolve_single_entrypoint`。既存 `evolve-reflect` にフラグを1つ足す） |
| 確認方法 | 反映先ファイルを読み、起草した行の**正規化後の完全一致**が存在するか（LLM 不要・決定論）。正規化規則は §6.2 |
| 不一致のとき | `applied` を書かない。`promoted` のまま残し `apply_unverified` を返す。**黙って成功にしない** |
| ストア追加 | **なし**。`reflect_status` の既存フィールド値だけで表現する（#379 凍結に非抵触・§7） |
| 副作用 | §6 表のとおり `reflect.py:327` の再発回数が今より小さく出る（＝これまで水増しされていた） |

#### 関数契約（codex cold review 2巡目 [Must]7・具体化）

当初は「引数に `target_path` と `draft_line` を足す」としか書いておらず、**追加フラグ名 / 対象
correction の指定方法 / 複数件更新時の対応 / `--apply-all` との呼出し形式**が未設計だった
（[Must]）。既存呼び出し（`--skip-all` → `update_reflect_status(filepath, indices, "skipped")`、
`reflect.py:1009`）と契約が壊れないよう、以下のとおり確定する。

**関数シグネチャの変更**（後方互換を保つ。`target_path`/`draft_line` はキーワード専用引数で既定 `None`）:

```python
def update_reflect_status(
    filepath: Path,
    indices: list[int],
    status: str,
    *,
    target_path: str | None = None,
    draft_line: str | None = None,
) -> dict:
    """status="applied" のときのみ target_path/draft_line が必須（無ければ ValueError）。
    §6.2 の正規化規則で target_path を読み、draft_line の完全一致を確認してから書く。
    不一致なら reflect_status は変更せず {"status": "apply_unverified", ...} を返す。
    status="skipped" 等は従来どおり target_path/draft_line 不要（--skip-all は無改修で動く）。
    戻り値: {"status": "applied" | "apply_unverified" | "skipped", "target": str | None, "reason": str | None}
    """
```

**新設 CLI フラグ**（既存 `evolve-reflect` に3つ追加。1呼び出し＝1 correction・1 target_path・
1 draft_line の**1:1対応に固定**することで、codex が指摘した「複数件更新時の target_path と
draft_line の対応」問題自体を発生させない — 朝の設問は1件処理するごとに Edit→CLI 呼び出しを
行うので、そもそも1回のCLI呼び出しで複数件をまとめて処理する必要が無い）:

| フラグ | 内容 |
|---|---|
| `--apply <source_correction_id>` | 対象 correction の識別子。**新しい識別子は作らない** — 既存の `make_source_correction_id(session_id, timestamp)`（`reflect.py`、`--view` 出力の `source_correction_id` フィールドと同一形式）を再利用する |
| `--target-path <path>` | 反映先ファイル（絶対 or リポジトリ相対）。agent が Edit 直前に確定した値をそのまま渡す |
| `--draft-line-file <path>` | 起草行の**全文**（§6.2 の表示用省略とは別の照合用全文）を含む一時ファイルのパス。シェル引数のクォート/エスケープ事故を避けるため、CLI 引数に直接テキストを渡さない（既存の `--corrections-file` 等ファイルパス系フラグと同じ規約） |

**`--apply-all` との関係**: 既存 `--apply-all`（`reflect.py:645`）は pending フロー（Step 5）で
`entry["apply"] = confidence >= min_confidence` を出力に付与する**表示専用フラグ**であり、
それ自体は `update_reflect_status` を呼ばない（agent が出力を見て Edit → 新フラグ `--apply` を
呼ぶ、という2段構成は変えない）。SKILL.md 側の手順変更は下表「迂回口を塞ぐ」参照。

**迂回口を塞ぐ（codex [Must]・T4 実測）**: `applied` を書く経路は**3つ**あり、うち2つは
Python を通らない。ゲートを1箇所に埋めるだけでは素通りする。

| # | 経路 | 場所 | 対処 |
|---|---|---|---|
| 1 | Python writer（`promote()` 本体） | `correction_semantic/promote.py:371` | ゲート内蔵可。**ここから `applied` を直書きするのをやめ、`promoted` を書く**（反映は後段） |
| 2 | **agent が JSONL を直接 Edit**（`--apply-all` 手順） | `skills/reflect/SKILL.md:83` | **不可（これが迂回口）**。手順を「agent が直接 Edit」から**「`evolve-reflect --apply <source_correction_id> --target-path <path> --draft-line-file <file>` を呼ぶ」に書き換える** |
| 3 | **agent が JSONL を直接 Edit**（approve / edit 手順） | `skills/reflect/SKILL.md:151-152` | 同上（同じ `--apply` フラグ） |

現状 `update_reflect_status()` は CLI からは `--skip-all`（`"skipped"` のみ）でしか呼ばれず、
**`applied` を書く CLI 入口が存在しない**。よって「フラグを足す」は必須作業であって選択肢ではない。

**これは `learning_skill_md_must_not_enforcement` の型そのもの**（SKILL.md に MUST と書いても
手順は飛ばされる）。**文書の書き換えだけに依存せず、CLI 側のゲートで担保する**。

---

### 6.2 一致判定の正規化規則（codex [Must]・T7 実測）

当初案の「前後空白と行頭 `- ` のみ」では、**箇条書きを使わないルールファイルで機能しない**。

実測（`~/.claude/rules/*.md` 33 件 + `<repo>/.claude/rules/*.md` 14 件 = 47 ファイル・本文 282 行）:

| 行の形 | 件数 | 割合 |
|---|---|---|
| `- ` 始まり（箇条書き） | 208 | 74% |
| 見出し（`#`） | 47 | 17% |
| **素の文（箇条書きでない段落）** | **27** | **10%** |
| `1. ` / `- [ ] ` / `> ` / 表 | 0 | 0% |

ファイル単位では **10 / 47 ファイル（21%）が箇条書きを一切使わない**
（`tdd-first.md` / `root-cause-first.md` / `git-push.md` など）。複数行にまたがる項目は実質 0 件。

**採る規則**:

1. 対象ファイルを読み、`- ` 始まりの行が **1 行でもあれば**「箇条書きファイル」と判定 →
   前後空白 + 行頭 `- ` を除去して完全一致
2. `- ` 始まりの行が **0 行なら**「素の文ファイル」と判定 → 前後空白の除去のみで**行全体の完全一致**
3. 番号付き・チェックボックス・引用は**実データに 0 件**なので初期実装では扱わない。
   ただし将来出現しうるので、**未知の行頭記号に当たったら「一致なし」でなく `apply_unverified` を返す**
   （黙って失敗にしない）

**照合に使うのは起草行の全文**。§4.3 で表示する `draft_line` は U8 のとおり60字で省略しうるが、
**表示用の省略文と照合用の全文は別に保持する**（省略文で照合しない）。

**壊して赤くする検査（2方向・`~/.claude/rules/verify-checks-by-breaking.md`）**:

1. 確認を無効化（常に True を返す）→ **赤**（書いていないのに `applied` が付くケースを検出）
2. 確認を常に False にする → **赤**（正しく書いたのに `applied` が付かないケースを検出）
3. さらに `promote.py` が `applied` を直書きするよう戻す → **赤**（ゲートの迂回経路を検出）

1 だけでは「確認関数を呼んだ回数」を数えるだけのテストが素通りする。3 は迂回経路の検査で、
§6 の状態分離だけでは守れない（**通常ロジックのみで守られている契約なので契約テストが必須**）。

**この proxy が無い場合に何が起きるか**（tacchi の指摘の言い換え）: 選択肢を4つに増やしても、
「共通ルールに書く」を選んだのに書かれなかった指摘が `applied` として記録され、
戦果ボードの採用件数だけが増える。**柱4「表示する数字が嘘をつかない」に直接抵触する。**

---

## 7. #379 新設凍結との整合（受入条件2）

**新しい store / observability section / advisory proposal adapter / weak_signal channel を1つも増やさない。**
`scripts/lib/shrink_freeze.py` を実際に読んで確認した。

| 凍結対象 | 現況 [コード] | 本設計での増減 |
|---|---|---|
| `FROZEN_STORES` | 44 件（`corrections.jsonl` / `correction_review_seen.jsonl` / `correction_idioms.jsonl` を含む） | **+1（codex 3巡目 [Must]B-2 で訂正）**。§12 決定4 で `optimize_history/<slug>.jsonl` を追加登録するため、実装後は **45件**になる。これは「新設」ではなく既存物理ストアの追認（`shrink_freeze.py:55-60` の前例。§12 決定4 に引用済み）なので #379 凍結の趣旨には抵触しない — ただし数として `FROZEN_STORES` 自体は変わる（±0 ではない）ので、下の検証コマンドは実装後に**フリーズ集合を44→45に更新した状態**で緑になる |
| `FROZEN_OBSERVABILITY_SECTIONS` | 44 件（うち 32 件は `CULLED_OBSERVABILITY_SECTIONS` で表示淘汰中） | **±0**。新しい section を作らない |
| `FROZEN_ADVISORY_PROPOSAL_ADAPTERS` | 2 件（`invalid_frontmatter` / `testpaths_coverage`） | **±0**。adapter を足さない |
| `FROZEN_WEAK_SIGNAL_CHANNELS` | 6 件（`esc_interrupt` / `llm_judge` / `manual_edit_after_ai` / `permission_deny` / `rephrase` / `verbosity`） | **±0**。既存 channel の中身に手を入れない |

- 凍結は有効（`SHRINK_FREEZE_ACTIVE: bool = True`・`shrink_freeze.py:44`）。
- 増やすのは **既存ストアのフィールド値**（`reflect_status` の新値 `promoted`、`record_reviewed` の
  `decision` 文字列）だけ。`assert_no_new_keys`（`shrink_freeze.py:259-275`）はストア名・section 名・
  adapter 名・channel 名を見るので、**フィールド値の追加は検出対象外＝違反にならない**。
  **`FROZEN_STORES` 自体への `optimize_history/<slug>.jsonl` 追加は例外**（上表参照。既存ストアの
  登録漏れの追認であり `assert_no_new_keys` が守る「新設禁止」の対象外）。
- 検証コマンド（実行済み・2026-08-17）: `python3 -m pytest scripts/lib/tests/test_shrink_freeze.py -q -n 0`
  → **13 passed（現状 = `optimize_history` 追加前）**。実装で `FROZEN_STORES` に
  `optimize_history/<slug>.jsonl` を追加した**後**も同じコマンドが緑であることを受入条件の証拠にする
  （追加前のまま実装すると `store_registry` の live 集合が `FROZEN_STORES` を超え、このテストが
  赤くなる設計になっている — これ自体が §12 決定4 を実装が飛ばしていないかの機械チェックになる）。
- `idiom_autopromote` は**解凍しない**（P10）。本設計は「人間に聞く」形なので復活させる必要がない。

**1点だけ注意（新設ではないが記録する）**: 採用履歴 `optimize_history` は `store_registry` 未登録で
`store_write` barrier も通らない（P16）。§8 でここに revert payload を足す場合、
「新設ストア」には当たらないが、未登録のままエントリ形式を拡張することになる。
是非は #434 の事前契約ゲートの論点で、本 issue のスコープ外（§11-U6）。

---

## 8. 取り消し手段（issue 必須要件2・受入条件3）

### 8.1 現状: rule 文書は revert 経路に乗らない

[コード] P14/P15。`scope="global"` は `~/.claude/skills` に解決される（`_target.py:57-59` →
`evolve_decision_ids.py:341-344`）ので、`~/.claude/rules/*.md` は `REASON_NOT_FOUND` か
`REASON_ESCAPES_ROOT` で必ず失敗する。理由ラベル（`_availability.py:50-53`）自身が
「この種類の採用（rules / hooks 等）は戻す機能の対象外です（skill の採用のみ対象）」と明言している。

[実測] P20: fleet 全体で戻せる採用は **1 件だけ**（42 件中）。revert lane はまだほとんど実績が無い。

### 8.2 採る案（最小拡張）: **既存 rule ファイルへの追記だけを revert 対応にする**

| 拡張点 | 内容 | 追加コスト見積 |
|---|---|---|
| root 解決 | `_target.resolve_target` に scope 2 種（`global_rule` → `~/.claude/rules` / `project_rule` → `<repo>/.claude/rules`）を追加 | 分岐2本。containment 検査・lstat・nlink 検査は既存のまま流用 |
| availability | `_availability._SUPPORTED_SCOPES` に同2種を追加 | 定数1行 |
| 記録 | 反映時に `optimize_history` へ既存形式の entry を append（`revert_schema_version` / `revert_before_b64` / `relative_path` / `scope`） | 既存 `append_history_entry_deduped` を呼ぶだけ |
| **後続変更検知（`--list` 表示・codex cold review 2巡目 [Must]8）** | `bin/evolve-revert --list` の一覧表示に、対象ファイルの**現在の SHA256** を読み `after_sha` と比較する処理を追加し、「戻せる」/「後続変更ありで戻せない」を出す。**`compute_revert_availability`（`_availability.py:41-89`）自体は変更しない** — 同関数はメタデータのみを見る純粋関数（I/O ゼロ）と明記されており（同ファイル冒頭の docstring）、その設計は保つ。**追加するのは `--list` の出力整形レイヤー（新しい読み取り専用ステップ）** であり、既存の理由コード3種（`pre_extension`/`lane_unsupported`/`before_too_large`）とは別に、`available=True` の entry についてのみ「後続変更あり」の注記を追加する | 新しい read-only 関数1つ（対象ファイルを読んで SHA256 を計算し `after_sha` と比較するだけ）。`_apply.py:294-301` の conflict 判定と同じロジックを listing 時点で流用できる |
| **やらないこと** | **新規ファイル作成の revert**（before 本文が存在しないため「不在」sentinel と schema version 2 が要る。#467 §1.4 が同じ穴を指摘済み） | ここは実装しない |

**帰結**: 新規 rule ファイルを作る反映と、Other 経由の hook 反映は **戻せない**。
受入条件は「1コマンドで戻せる、**または戻せない場合はその制約を明示する**」なので、
**選択時に「この反映は戻せません」と明示して再確認する**ことで満たす（§5 の hook 行と同じ扱い）。

### 8.3 conflict の実条件（codex [Must]・T5 実測）— 「1コマンドで戻せる」には条件が付く

`evolve_revert/_apply.py:294-301` は**ファイル全体の SHA256** を比較する:

| 現在の状態 | 判定 |
|---|---|
| `current_sha == after_sha`（採用直後から変わっていない） | **通常 revert 可** |
| `current_sha == before_sha`（既に戻っている） | 冪等（何もしない） |
| **どちらとも不一致** | **`BRANCH_CONFLICT`。書込みゼロで中止** |

**帰結**: 「1行追記して、その後に**同じファイルへ別の1行が追記された**」状態では、
**最初の1行だけを戻すことはできない**。行単位の revert ではないため。

そして本設計は `~/.claude/rules/*.md` を**繰り返し追記する**ものなので、
同じファイルへ2件目を反映した時点で1件目は戻せなくなる。

**採る扱い（正直に条件を明示する）**:

- 受入条件は「1コマンドで戻せる、**または戻せない場合はその制約を明示する**」なので、
  **`bin/evolve-revert --list` の各行に「戻せる / 後続変更ありで戻せない」を出す**
- 朝の設問で反映先を選ぶ時点では「戻せます」と断定しない。文言は
  **「あとで戻せます（同じファイルをその後に変更していない場合）」**にする
- 行単位 revert への拡張は**やらない**（`revert_schema_version` 2 相当の設計が要り、
  #467 §1.4 と同じ穴を開けることになる）。§12 の決定3 にこの選択肢を追記した

**運用上の含意**: 「既存ファイルに追記」を既定にすると、ほぼ全ての rule 反映が戻せる側に入る。
グローバル rules は現在 **33 ファイル**あり（`ls ~/.claude/rules/*.md | wc -l` → 33・2026-08-16 [実測]）、
テーマ別に揃っているので新規作成が必要な場面は多くない。

---

## 9. 既存 reflect スキルへの接続（新しい工程を作らない）

原則どおり **既存工程に接続する**。新規スキル・新規サブコマンドは作らない
（`feedback_evolve_single_entrypoint`）。

| 接続先 | file:line | 何をするか |
|---|---|---|
| 朝の設問テンプレ | `skills/evolve/references/correction-review.md:64-68`（Step 6.2 の y/n 分岐） | ここを §4.3 の4択に差し替える。**このファイルが分岐条件・AskUserQuestion テンプレの正典**（同ファイル :3 に明記） |
| SKILL.md の1行要約 | `skills/evolve/SKILL.md:255` | 「y/n 確認」→「反映先つき選択」に文言を合わせる（要約のみ・手順は上の references 側） |
| 書き込み規約 | `skills/reflect/SKILL.md:168-174`（「書き込み時のルール」） | **再発明しない**。既存/新規の分岐・`routing_hint` の扱い・`line_limit_warning` の扱いをそのまま引く |
| ~~反映先の並べ替え~~ | ~~`scripts/lib/reflect_routing.py:108`（`suggest_claude_file`）~~ | **削除（codex cold review 2巡目 [Must]1）**。§4.2 で並べ替えを廃止したため、この行は矛盾したまま残っていた誤り。`suggest_claude_file` はこの新設フロー（promoted の朝の設問）では**呼ばない**。従来どおり **既存の pending フロー（reflect Step 6・P5）でのみ**使われ続ける（本設計のスコープ外・変更しない）。**新設フローで、共通/PJルールのどのファイルに書くか**は、既存の書き込み規約（`skills/reflect/SKILL.md:172-178`）と同じ判断（agent が Edit/Write 前に既存ファイルの内容を見て決める）を流用する。新しい選定ロジックは作らない |
| 昇格 CLI | `evolve-reflect --promote-weak`（`correction-review.md:65`） | そのまま使う。`reflect_status` の値だけ §6 に従って分岐させる |
| 既読記録 | `correction_semantic/daily_review.py:111`（`record_reviewed`） | `decision` に反映先を入れて呼ぶ（自由文字列・P9）。**部分失敗時は `promoted_keys` のみ渡す既存 MUST を維持**（#326） |

**新しい工程を作る必要があるか**: 無い。反映の手順（Edit の作法・行数上限・新規/既存の分岐）は
reflect スキルが既に全部持っている（P5）。本設計がやるのは
**「その手順を朝の設問の直後に呼ぶ」ことと「promote が入力集合から外さないようにする」ことだけ**。

---

## 10. 失敗モードと、どう気づくか（気づけないものは正直に書く）

| # | 失敗モード | 気づけるか | 検出方法 |
|---|---|---|---|
| F1 | agent が SKILL.md の4択手順を飛ばし、従来どおり y/n だけ出す | **手動監査でのみ気づける**（codex [Must] で訂正。当初「気づける」と書いたが検出器が無い） | `record_reviewed` の `decision` 分布を**人が見れば**分かる。異常判定も通知も存在しない。`learning_skill_md_must_not_enforcement` の既知パターン |
| F2 | 4択は出たが、いつも「いまは反映しない」が選ばれる | **区別できない（設計の限界）** | F1 と同じ分布を見ても「手順が出ていない」と「出たが毎回3を選んだ」は同じ形にならない（F1 は reflect_status が `applied` のまま、F2 は `promoted`）ので**区別はできる**が、「なぜ3ばかりなのか」は測れない。文言が悪いのかユーザーの意思なのかは**気づけない** |
| F3 | rule ファイルに書いたが 10 行上限を超えて壊れる | **書く前に警告が出る。超過後の検出器は無い**（codex [Must] で訂正） | `check_line_limit`（`line_limit.py:81`）+ `reflect.py:253-262` の `line_limit_warning` は**書込み前**の警告。警告を無視して書いた後に気づく仕組みは無い |
| F4 | 反映先を間違えた（PJ 固有の話を共通ルールに書いた） | **気づけない**（codex [Must] で訂正。当初「気づける」は誤り） | revert は**戻す手段**であって誤ルーティングの**検出器ではない**。間違いに気づくのは人が読んだときだけ |
| F5 | 新規 rule ファイルを作る反映をして、戻せないことに後で気づく | **気づける（事前に）** | 選択時に「この反映は戻せません」を出す（§5・§8.2） |
| F6 | `reflect_status` に新値を入れたことで、洗い出せていない reader が壊れる | **部分的にしか気づけない** | §6 の表は `grep -rn reflect_status scripts/ hooks/ skills/ bin/` の全件（tests 除く）に基づく。**tests 配下と外部ツールは見ていない**ので取りこぼしうる。フルスイート（`python3 -m pytest`）が最後の網 |
| F7 | 反映したルールが実際には行動を変えていない | **気づけない** | U3。因果測定 (b) は分母が揃っておらず `not_measured`。本設計はここを解決しない。**「届いた」ことしか測れず「効いた」ことは測れない**と明記しておく |
| F8 | 「いまは反映しない」が毎朝再提示されて承認疲れを起こす | **解消済み** | §5.1 で `record_reviewed(decision="deferred")` を呼び既読にするため、朝には再提示されない（reflect でまとめて出る） |
| F9 | 「共通ルールに書く」を選んだのに、実際にはファイルに書かれていない | **気づける（tacchi Must-1 で追加）** | §6.1 の実在確認。不一致なら `applied` を付けず `apply_unverified` を返す。この proxy が無ければ**気づけないまま採用件数だけ増える** |
| F10 | 起草した1行（事前提示）と、実際に書かれた行が違う | **気づける** | §6.1 の確認が「起草行の正規化後完全一致」を見るので、書き換えられていれば `applied` が付かない。ただし**agent が起草行そのものを書き換えてから提示・書込みした場合は検出できない**（提示と書込みが同じ agent なので） |
| F11 | ~~skill / hook を「回した」と言われて実際には渡らない~~ | **解消済み** | T2 で受け渡し口が実在しないと確定したため、**「回す」と言うのをやめた**（§4.2・§5）。tacchi 2巡目 Must-2 を受け、単に「対象外です」で終わらせず**実行可能な次の一手**（`/evolve-anything:evolve-skill` / `hooks/` 手編集）を案内する（§4.4）ので空約束が発生しない |
| F12 | 同じ rule ファイルに2件目を反映したせいで、1件目が戻せなくなる | **気づける** | §8.3。`bin/evolve-revert --list` に「後続変更ありで戻せない」を出す。ただし**戻せなくなった瞬間には通知しない**（一覧を見たときに分かる） |
| F13 | 移行バッチ（§4.6）を実行しないまま実装され、過去167件が永久に不可視のまま残る | **気づける（tacchi 2巡目 Should-3 で追加）** | §4.6 の壊して赤くする検査: 移行バッチ未実行なら167件は `applied` のまま `reflect_data_count` にも `("pending","promoted")` の集合にも入らず、`/reflect` 提案が発火しない。実装時のリリース前チェックリストに「移行バッチ実行 → `reflect_data_count` に反映されることを確認」を1項目として加える |

---

## 11. 未確認事項（空欄にしない）

| # | 事項 | 状態 | 何が変わりうるか |
|---|---|---|---|
| ~~U1~~ | `AskUserQuestion` の選択肢上限 | **確定（2026-08-16）**: 選択肢は **2〜4個**、これに **Other が自動付与**される。tool の入力スキーマ（`options` の `minItems: 2` / `maxItems: 4`、および「ユーザーは常に Other を選んで自由記述できる」の記載）で確認 | §4.2 の固定4択 + Other はこの上限ちょうどに収まる。**変更不要** |
| ~~U2~~ | Other の自由記述から memory / pitfall へ確実に到達できるか | **決定済み（2026-08-17 ユーザー確定）**: Other は自由記述で受ける。**memory / pitfall を初期スコープから落とす案は採らない**（旧案の「対象外に落とす」フォールバックを撤回）。agent が自由記述を memory / pitfall のどちらに解釈すべきか判断が付かない場合は、**黙って対象外にせず、その場で1回だけ聞き返す**（例:「memory と pitfall のどちらに書きますか？」の追加1問）。聞き返しても不明なら初めて「対象外です」と表示する | 実装時の最初の数件で「聞き返し」がどのくらいの頻度で発生するかを観測し、頻発するようなら自由記述の解釈規則（キーワードマッチ等）を足す判断材料にする（本設計のスコープ外・別途観測） |
| U3 | 反映したルールが行動を変えたか | **測定不能（現時点）** | ADR-054 の (b) が計測可能になるまで判定不能 |
| ~~U4~~ | `suggest_claude_file` の並べ替え精度 | **論点ごと消滅（2026-08-17 再実測でも同一結論）**。167 件中 161 件（96.4%）が `None` を返し、返り値は候補1つのみ（リストでない）。**並べ替えという機能が存在しない** | §4.2 を固定4択に変更した。**「上位2件に正解が入る率」は正解データ（人間が実際にどこへ書いたか）が corrections.jsonl に無いため、そもそも測定不能**でもある |
| U5 | 他 PJ のストアでも同じ分布か | **未実測**。[実測] 値はすべてこのマシン 1 台 | 他環境では件数が違う。ただし §3.2 の「0 件」は構造証明なので環境に依存しない |
| ~~U6~~ | `optimize_history` が `store_registry` 未登録のまま rule writer を足してよいか | **決定済み（2026-08-17 ユーザー確定）**: 先に登録する。§12 決定4 に登録内容（`name`/`writer`/`classification` 等）を具体化して記載した | — |
| ~~U7~~ | Other で skill / hook を既存レーンへ受け渡せるか | **確定（2026-08-16 実測）: 受け渡し口は存在しない**。`hook_candidates` は `proposal_lane_unconnected_baseline.txt:4` に**未接続として公式登録済み**で読み手ゼロ。朝の y/n が読むのは `matched_skills` と `skill_evolve` の2種のみ（`evolve_decisions/_candidates.py:97,109`）。`skill_evolve` 側の関数は「既存スキルに自己進化テンプレを入れるか」の判定用で、correction を渡す口ではない | 「回す」と言わない設計に変更した（§4.2・§5）。**空約束が消えたので受入条件への影響なし** |
| U8 | 起草1行の事前提示（tacchi Must-6）が1問400字以内に収まるか | **実測済み（2026-08-17・codex cold review 2〜3巡目 + tacchi 2巡目。§11-A に詳細）**。§4.3 の detail 文言から § 番号・file:line・内部語を落とし、Other 行を表から除いた（tool 側が自動付与するため detail を持てない・§4.3 参照）ことで、**detail 込みの表示でも1問あたり平均357字・最大377字**となり、400字以内に収まることを確認した | §11-A 参照。設計の骨格（起草1行の事前提示・固定4択）は変えない |
| U9（新設） | 「利用者が2日目以降は label/detail を読み飛ばし、変動部分だけ確認する」という運用が実際に起きるか。「5問30秒」の実際の体感時間 | **測定不能（設計時点。codex 3巡目 [Must]で依拠前提から除外）**。§11-A のとおり文字数からの逆算では「5問30秒」に届かない（推定1分45秒〜4分28秒）。読み飛ばしが実際に起きるかは行動観察が要り、設計文書だけでは測れない | 実装後、初回実運用の数日で `record_reviewed` の応答時刻間隔（設問表示〜回答）を見れば体感時間の実測が取れる（新しいログは不要・既存タイムスタンプで導出可能）。読み飛ばしが起きず遅いままなら detail をさらに削る判断材料にする |
| U10（新設） | §4.3.2 の新規ファイル確認（3択）が朝の設問に追加される頻度 | **未観測（tacchi 2巡目 [Nit]）**。「通常フローの設問数には数えない」としているが、最悪ケースは「5問 + 新規ファイル確認 + Other 聞き返し」で7問になりうる。「数えない」は帳簿上の整理であって、利用者の体感時間が増えないことを意味しない | §8.3 の「グローバル rules は既に33ファイルあり新規作成は稀」という前提が外れているかどうかを、実装後に新規ファイル確認の発生率で確認する |

### 11-A. U8 実測: 既存 correction 5件で表示サンプルを実際に作る（codex cold review 2〜3巡目 + tacchi 2巡目 [Should]）

**既存の `reflect_confirmed` correction 5件**（2026-08-14〜08-16 実データ・M0 と同一ストア）を使い、
実際に4択の表示を組み立てて字数を測った。message は元レコードの引用（個人パス等は含まない短文のみ選定）、
`draft_line` は agent 役として著者が起草した（§4.3 の設計どおり、選ばせる前に用意する想定の1行）。

| # | 元 correction（`reflect_confirmed`・実データ） | 起草した反映先 | 選ばれると想定する選択肢 |
|---|---|---|---|
| 1 | 「spec-keeperはcodexレビューいらない」（2026-08-14） | `spec-keeper 等 docs-only の PR は codex レビュー不要（コード変更 PR のみ codex を標準挿入する）` | 1（共通ルール。実際 `~/.claude/rules/no-codex-review-for-spec-keeper.md` として既に採用済みの内容と一致 — `feedback_no_codex_review_for_spec_keeper.md`） |
| 2 | 「フルスイートでなんで頭でまわしちゃったの？token無駄じゃん」（2026-08-16） | `並行 worker には python3 -m pytest -n 0（直列）を指示する。フルスイートを頭で回さない` | 2（PJルール。evolve-anything 固有の運用） |
| 3 | 「選べる道のメリット、デメリット考えて材料がそろってから提案してほしい。codexにも相談してみて」（2026-08-14） | （既存の `explain-clearly.md` と重複のため追記不要） | 3（いまは反映しない・重複検出） |
| 4 | 「自動でルール化を全部ユーザーにrule化するか確認すれば誤爆はふせげるよね…」（2026-08-16） | （本設計 #475 自体がこの指摘の実装なので pitfall として経緯のみ残す） | Other（pitfall） |
| 5 | 「なんで、V5がOKっていってたの？純粋に未着手が５件もあるのに」（2026-08-14） | （特定タスクの一回性の指摘のため一般化しない） | 4（いいえ） |

**1回目の実測（当初の §4.3 案）**: detail に § 番号・file:line・「回しません」等の内部語が入り、
かつ実際には表示されない Other の detail 文言まで含めていたため、平均793字と大きく超過していた
（[Must]で codex 3巡目・tacchi 2巡目とも指摘）。detail 文言を tacchi の案どおり書き換え
（§4.3 参照。§ 番号を全て除去・Other 行を表から削除・revert の詳細条件を §4.3.1 の完了メッセージへ
移動）、**再測定**した結果が以下（Python `len()`。`message`/`draft_line` とも全角60字で切る前提。
実行コマンドと5件個別の出力は
[artifacts/475-measurement-log-20260817.md](artifacts/475-measurement-log-20260817.md) を参照）:

| 表示範囲 | 5件の実測値（字） | 平均 | 最大 | 400字以内か |
|---|---|---|---|---|
| 指摘文 + 起草1行 + 質問文 + Other誘導1行（選択肢を含まない） | 155 / 158 / 152 / 172 / 123 | 152字 | 172字 | ✓ |
| 上記 + 選択肢 label のみ（detail 抜き） | 213 / 216 / 210 / 230 / 181 | 210字 | 230字 | ✓ |
| 上記 + 選択肢 detail 全文（実際に表示される全文） | 360 / 363 / 357 / 377 / 328 | 357字 | 377字 | **✓（1回目の793字から408字削減）** |

**codex 3巡目 [Must]（B-1・「5問30秒」は文字数しか測っておらず所要時間を測っていない）への対応**:
文字数以外に「読む時間」を主張するなら、それは実測ではなく推測になる。以下は**推測であることを
明記した上で**参考値として併記する（合否判定には使わない — 合否は上表の文字数のみで判定する）:

| 読み方 | 5問合計の文字数 | 推定所要時間（400字/分〜600字/分・一般的な日本語黙読速度の目安） |
|---|---|---|
| label のみを読んで判断 | 約1050字 | 約1分45秒〜2分37秒 |
| detail まで全文読む | 約1785字 | 約2分58秒〜4分28秒 |

**分かったこと**: 上記どちらの読み方でも「5問30秒」には届かない。**この「30秒」という目標は
文字数×読字速度から導出できる数値ではなく、`CLAUDE.md` 自身が明記するとおり
「到達目標であって現状説明ではない」**（`CLAUDE.md` の該当節）。よって:

- **設計の合否判定は測れるもの（1問あたりの文字数・400字以内）だけに置く。** これは上表のとおり
  detail 込みでも達成している。
- **「利用者が2日目以降は読み飛ばす」という仮定は、設計が依拠する前提から外す**（codex 3巡目 [Must]）。
  §4.2 の「位置を固定する」設計判断そのものは維持するが、それによって実際に読み飛ばしが起きるか・
  どのくらいの体感時間になるかは**実装後の観測項目**として §11 U9（新設）へ移す。
- 1回目の実測で見つかった「detail の主要コストは起草1行ではなく固定文言そのもの」という発見
  （§ 番号・内部語・冗長な条件説明）は今回の反映で解消された。この知見自体は正しかった。

**サンプルの再現方法**: 上記5件の message は `~/.claude/evolve-anything/corrections.jsonl` の
`reflect_confirmed` レコードから `timestamp` で絞って抽出した（M0 と同一ストア）。draft_line・
想定選択肢は著者による手作業の割当てで、実装が実際にこの5件をこう分類することを保証するもの
ではない（U2 とも実装時の実測が必要な点は変わらない）。

---

## 12. 決定事項（2026-08-17 ユーザー確定・codex cold review 2巡目 [Must]10・11 反映）

**以下4点はすべてユーザー判断が確定した。「判断待ち」ではなく決定として扱う。**

### 決定1: 4つ目の選択肢を「いいえ」にするか → tacchi Must-5 の時点で決定済み

「いいえ」は**常設**する（反映先候補は2つ）。却下手段が無いと不要な指摘が毎朝出続け、
承認疲れが1日5件の上限（P8）を超える。`record_reviewed(decision="rejected")` は既存機能（P9）。

### 決定2: 「いまは反映しない」を選んだ記録を、90日で消すか残すか → **消さない**

`prune/corrections.py:84` の削除対象集合に `promoted` を**入れない**（実装は既存の
`status not in ("applied","skipped")` 判定に `promoted` が自然に含まれる＝**変更不要**。
既に `("applied","skipped")` の外にある値なので、何もしなければ保持される）。

- 理由: 選択肢の文言で「あとで反映できます」と約束するのに、90日で黙って消えるのは
  issue 必須要件 1b（実態を偽らない）に反する。
- 選ばなかった場合（消す）に何が起きたか: 「あとで判断」が予告なく消える。
  **2026-09-10 前後に 2026-06-12 分の 41 件が消える見込みだった**（§3.4(b)）。
- 副作用（採用した「消さない」側）: `corrections.jsonl` が単調増加する。現在
  **177 行 / 223 KB**（[実測] 2026-08-17・M0）なので、当面（数年規模）問題にならない。

### 決定3: rule 反映の revert 対応をどこまでやるか → **既存 rule ファイルへの追記のみ対応**

**新規ファイル作成は「戻せません」と画面に明示する（§4.3・§8.2）。行単位 revert は別 issue に切る。**

- 理由: 既存ファイル追記なら before 本文が存在するので、現行 revert の3分岐
  （normal / 冪等 / conflict）がそのまま使える。追加は root 解決の分岐2本と定数1行だけ。
- 選ばなかった場合（フル対応）に何が起きたか: 「不在」sentinel + `revert_schema_version` 2 の
  導入が要る。これは #467 §1.4 が hook 提案について指摘した穴と**同じ穴**であり、本 issue 単独で
  塞ぐには影響範囲が大きすぎる。
- 選ばなかった場合（revert 対応しない）に何が起きたか: 受入条件3は「制約を明示する」で
  形式的には満たせるが、**全PJに効く共通ルールを戻せない**のは影響範囲的に危うい
  （issue 必須要件2 の指摘どおり）。
- 選ばなかった場合（行単位 revert）に何が起きたか: ファイル全体 SHA でなく「その1行が今もあるか」
  で判定すれば、同じファイルに2件目を反映しても1件目を戻せる。ただし `evolve_revert` の判定契約
  そのものを変えることになり、**skill 採用の revert にも影響する**ので影響範囲が大きい。別 issue。

### 決定4: 採用履歴ストアが「登録なし」のまま、新しい書き手を足してよいか → **足す前に登録する**

**codex cold review 2巡目 [Must]（頭の追加調査を反映）**: 当初「登録は宣言を書くだけでコード変更は
ほぼ無い」と書いたが**不正確だった**。実際には次の2点が要る。

1. `store_registry.py` の `_DECLARATIONS` に宣言を1件追加する（コード変更）。
2. **`shrink_freeze.FROZEN_STORES`（44件・`scripts/lib/shrink_freeze.py:61-108`）にも同じ名前を
   追加する必要がある**（`assert_no_new_keys` は `store_registry` の live 集合が `FROZEN_STORES` の
   部分集合であることを検査するため、登録するだけで live 集合が1件増え、追加しないと**凍結違反として
   reject される**）。

**これは #379 新設凍結には抵触しない**。`shrink_freeze.py:55-60` に前例が明文化されている:

> 「凍結の趣旨は『新しい書込経路・新しい機能を作らせない』ことであり、**既存の実ファイル・既存の
> writer/reader コードを registry へ追認する本件は『新設』ではない**」

`FROZEN_STORES` の round2（#399 codex Must 2）で `evolve-queue.json` 等 4 件が同じ理由で追加登録
された実績があり（同ファイル 47-58 行目のコメント）、`optimize_history` も**採用時点から実際に
書き込まれ続けていた既存ストアの登録漏れ**という点で同型。

**登録する文字列（実測して確定）**: `optimize_history` は**ディレクトリ**であり
（`~/.claude/evolve-anything/optimize_history/`）、中身は PJ ごとの `<repo-slug>.jsonl`
（実際のファイル: `evolve-anything.jsonl` / `sys-bots.jsonl` / 他。`.lock` ファイルは対象外）。
`FROZEN_STORES` には既に同じ「ディレクトリ + `<slug>`」形式の前例
（`remediation_suppression/<slug>.jsonl` / `remediation_surfaced/<slug>.json`）があるため、
**`optimize_history/<slug>.jsonl` として登録する**（単一ファイル名としては登録しない）。

`StoreDeclaration` の主要フィールド（`scripts/lib/store_registry.py:74-112`）:

| フィールド | 値 | 根拠 |
|---|---|---|
| `name` | `"optimize_history/<slug>.jsonl"` | 上記 |
| `writer` | genetic-prompt-optimizer（`optimize.py`）/ evolve-loop-orchestrator（`run_loop.py:685`）/ evolve-fitness（`fitness_evolution.py:246`）/ evolve_revert（`_apply.py:129`、revert 実行時の追記） | 全4呼び出し元を `grep -rn append_history_entry_deduped\|append_entry(` で確認済み（tests 除く） |
| `writer_locus` | `"batch"` | 全4箇所とも hooks/*.py 経由ではなくスキル/CLI から呼ばれる |
| `classification` | `"raw_event"` | 採用イベントの一次記録（派生キャッシュではない） |
| `retention` | `"permanent"` | prune 対象になっていない（#12 決定2 と同型の判断） |
| `status` | `"active"` | write barrier の書込み許可対象にする |

- 選ばなかった場合（登録せずに足す）に何が起きたか: 動きはするが、**このストアだけ検査の対象外**
  という状態が続く。#434 の議論が起きたときに、この設計が「前例」として引かれる。
- 選ばなかった場合（登録もせず記録も足さない）に何が起きたか: rule 反映が
  `bin/evolve-revert --list` に出ず、**受入条件3（戻せる）が満たせない**。

---

## 13. 受入条件との対応

| 受入条件 | 本設計での担保 | 証拠 |
|---|---|---|
| 朝の y/n で「はい」を選んだ項目が、同じ設問の中で反映先まで決まる | §4.3 の1問4択 + Other | 実装後に `record_reviewed` の `decision` 分布で確認 |
| 新しい store / observability section / advisory adapter / weak_signal channel を1つも追加しない | §7。**`FROZEN_STORES` は既存ストアの登録漏れ追認で 44→45（+1）になるが、これは「新設」の定義に当たらない**（前例あり・§12 決定4）。他3定数は ±0 | `python3 -m pytest scripts/lib/tests/test_shrink_freeze.py -q -n 0` → 13 passed（2026-08-17 実行済み・`optimize_history` 追加実装後も緑であることが受入条件） |
| 反映したルール行を1コマンドで戻せる（戻せない場合は制約を明示） | §8.2（既存ファイル追記は戻せる / 新規作成・hook は明示して不可） | `bin/evolve-revert --list` に rule entry が出ること |
| 「昇格済み」と「反映済み」が別状態として区別される | §6（`promoted` / `applied`） | §6 の2方向 mutation テストが赤くなること |
| （tacchi 追加）**「反映済み」が実態と一致する** | §6.1（反映先ファイルに該当行が実在することを確認してから `applied`） | §6.1 の3方向 mutation テストが赤くなること + `apply_unverified` が実際に返る負ケースのテスト |
| （tacchi 2巡目追加）**設問の文言が利用者にそのまま理解できる（§番号・内部語を出さない）かつ1問400字以内** | §4.3（detail 文言の全面書き換え・Other 行の削除）+ §11-A（実測357〜377字） | §11-A の実測表（5件・最大377字）+ artifact の verbatim 出力（`python3` スクリプトで第三者が再計算可能） |

---

## 14. レビュー反映履歴

### tacchi（利用者・実態突合の視点）— 2026-08-16・**反映済み**

判定は「方向承認・修正要求つき」。核心の指摘:

> 4択化そのものは正しいが、病巣は「選んだ＝反映された、を誰も検証しない」こと。

| # | 指摘 | 反映先 | 反映内容 |
|---|---|---|---|
| Must-1 | `applied` を「反映先ファイルに該当行が実在する」確認の後に限定する決定論 proxy | **§6.1（新設）** | `update_reflect_status` に実在確認を内蔵。不一致なら `applied` を書かず `apply_unverified` を返す。3方向 mutation テストを受入条件に追加 |
| Must-2 | `reflect_status` の読者を全洗い | **§6** | 再 grep で**漏れ3件を発見**（`reflect.py:327` の再発回数集計 / `reflect.py:1006` の `--skip-all` / `skills/reflect/SKILL.md:83,151-154` の手順書自体）。表に追加し、SKILL.md が手順として `applied` を直書きさせている点は §6.1 の CLI 側ゲートで担保する方針に |
| Must-3 | 「記録のみ」の再浮上経路 | **§5.1（新設）** | 素案の「既読にしないので翌朝また出る」は**重複 append と `promoted` の消失**で成立しないことが判明。既読にした上で reflect の入力集合を `("pending","promoted")` に広げる案へ差し替え。§4.3 の文言も修正 |
| Must-4 | skill / hook を初期スコープから除外（revert 迂回の防止） | **§4.2 / §5** | 反映先を rule 2種 + memory + pitfall の4種に限定。skill は `skill_evolve` レーンとの二重記録、hook は revert 経路不在が理由 |
| Must-5 | 「いいえ」を常設 | **§4.3 / §12** | 選択肢3・4は並べ替え対象外の常設に。§12 決定1 を「決定済み」に格上げしユーザー判断から外した |
| Must-6 | 起草1行を事前提示 | **§4.3** | AskUserQuestion の question 本文に `draft_line` を入れる。agent はどのみち起草するので**作る順番を前に移すだけ**（新規生成器・LLM 追加呼び出しなし）。長さ制限は U8 |

**この反映で増えた未確認**: U7（Other の skill / hook が既存レーンへ実際に渡るか）/ U8（起草1行の提示長）。

### codex（正しさの視点）— 1巡目 2026-08-16・判定 `設計修正要`・[Must]15件 → **反映済み**

**構造の指摘8件**:

| # | 指摘 | 反映 |
|---|---|---|
| C1 | `applied` ゲートが迂回可能（`promote.py` 直書き + SKILL.md の agent 直接編集2箇所） | **§6.1** に3経路の表を追加。CLI 入口を新設（フラグ追加）し SKILL.md の手順を書き換える方針に |
| C2 | §6.1 の関数契約が未設計（`target` も `draft_line` も渡らず戻り値も無い） | **§6.1** にシグネチャ変更と戻り値を明記 |
| C3 | 正規化「前後空白 + 行頭 `- `」では正しく書いた行を見失う | **§6.2（新設）**。実測で 47 ファイル中 **10 が箇条書きを使わない**と判明。箇条書き有無で分岐する規則に。表示用の省略文と照合用の全文を分離 |
| C4 | §1.1 が記載どおりに再現しない（同日中に値が動く / P19 の本文がどのコマンドでも出ない） | **§1.1** に M0（sha256 + 行数の指紋）と M4（本文抽出コマンド）を追加。初版値と再測定値の差 +3 を明記 |
| C5 | revert は後続追記が1件でも入ると conflict に落ちる | **§8.3（新設）**。ファイル全体 SHA 比較であることを実測で確認し、「戻せます」の断定をやめ条件付き文言に |
| C6 | skill / hook の「既存レーンへ回す」が空約束 | **§4.2 / §5**。実測で受け渡し口の不在を確認（P21/P22）。**「回す」と言うのをやめ「対象外」と正直に表示**する設計に変更 |
| C7 | §10 の F1 / F3 / F4「気づける」に実際の検出器が無い | **§10** を訂正（手動監査のみ / 書込み前警告のみ / 気づけない） |
| C8 | `promoted` を足すと `/reflect` 提案が発火しなくなる | **§5.1**。当初「suppression は変えない」としたのは誤り。`reflect_data_count` が5件閾値の提案 MUST を駆動していることを実測で確認（P24）し、変更対象に追加 |

**「測れるのに測っていない」7件（1巡目時点）**: 全て実測し、**U1・U4・U7 は解決、U6 は §12 決定4
として提示、U2・U8 は測定条件のみ明記**（この時点では両方とも未決定・未実測のまま次巡へ持ち越し。
**2巡目で両方とも確定・実測済みに更新した** — 下記参照）（§11）。

**最大の設計変更**: `suggest_claude_file` による**並べ替えを廃止し固定4択にした**。
実測で同関数は 96.4% が `None` を返し、そもそも候補を1つしか返さない（リストでない）ため、
「上位2件を並べる」が実装上成立しないことが分かった（P23）。**固定にしたことで設計はむしろ単純になった。**

### codex（正しさの視点）— 2巡目 2026-08-17・判定 `設計修正要`・[Must]11件 + [Should]1件 → **反映済み**

1巡目の反映が**部分的**だった（文書の一部に旧方針の文言が残ったまま、実測値も同日中の変動を
拾い切れていなかった）ことを受けた再レビュー。指摘は大きく5カテゴリ。

**A. 文書内の反映漏れ（3件）**:

| # | 指摘 | 反映 |
|---|---|---|
| A1 | §9（旧・反映先の並べ替え行）が `suggest_claude_file` で「候補を並べ替える」としたまま残存 | **§9**。該当行を打ち消し線で無効化し、「この新設フローでは呼ばない」と明記。具体的なファイル選定は既存の書き込み規約（P5）を流用する旨を追加 |
| A2 | §4.3 Other 欄が「既存の採用フローへ回した旨を表示」としたまま残存（§4.2/§5 の「回さない」と矛盾） | **§4.3**。「対象外です」表示に統一。§4.2 T2 の結論と一致させた |
| A3 | P17〜P20・M0・§12 が旧 172行時点の値のまま、177行時点の値と同一文書内で競合 | **§1.1・§2.4・§12**。全実測値を 2026-08-17 の単一測定点（lines=177・M0 参照）に統一。全出力を artifact に固定 |

**B. 実測の誤り・再現性（3件）**:

| # | 指摘 | 反映 |
|---|---|---|
| B4 | P23「confidence 全件0.9固定」は correction 入力側の固定値と関数の返り値を混同した誤り | **§2.6 P23 直下**に訂正記録を追加。再測定で非 `None` の6件は全て `confidence: 0.85` と確定 |
| B5 | §1.1 M0 が再現可能でない（測定時点のスナップショットが無い） | **§1.1**。全出力を [artifacts/475-measurement-log-20260817.md](artifacts/475-measurement-log-20260817.md) に固定。生ストアの生コピーは repo に置かない判断とその理由を明記（個人パス・会話引用を含むため） |
| B6 | 証拠等級の不統一。P21/P22 は実ストア観測でなく構造証明なので `[コード]` が正しい | **§2.6**。P21/P22 を `[実測]` → `[コード]` に訂正。P23 に再現コマンド（§1.1 M5）を追加 |

**C. 契約の未設計（3件）**:

| # | 指摘 | 反映 |
|---|---|---|
| C7 | §6.1 の `applied` CLI 入口が未設計（フラグ名・識別方法・複数件対応・`--apply-all` との関係） | **§6.1**。`--apply`/`--target-path`/`--draft-line-file` の3フラグ + 関数シグネチャ変更を具体化。既存 `make_source_correction_id` を識別子に再利用し新識別子を作らない。1呼出し=1件に固定して複数件対応の曖昧さを消した |
| C8 | §8.3 の「後続変更あり」表示が §8.2 の変更一覧に反映されていない | **§8.2**。「後続変更検知（`--list` 表示）」行を追加。`compute_revert_availability` 自体は変更せず、`--list` 出力整形レイヤーを別途追加する設計に |
| C9 | revert の制約が実際の4択文言に反映されておらず、新規ファイル時の再確認タイミングも未定義 | **§4.3**。選択肢1・2の detail に「あとで戻せます（同ファイルへの後続変更が無い場合）」を追加。新規ファイル判明時は Edit 直前に別途1問追加確認する設計を明記（通常の朝の設問数には数えない） |

**D. 判断の確定（2件）**:

| # | 指摘 | 反映 |
|---|---|---|
| D10 | §12 判断2・3・4 が「ユーザー判断待ち」のまま | **§12**。ユーザー確定を反映し「決定事項」に格上げ。判断4は登録先文字列（`optimize_history/<slug>.jsonl`）・`StoreDeclaration` の主要フィールドまで具体化 |
| D11 | §11 U2 が実装時確認に送られたまま | **§11**。「Other 自由記述で受け、判断が付かない場合は聞き返す。memory/pitfall を対象外に落とす案は不採用」に確定 |

**E. [Should]1件**:

| # | 指摘 | 反映 |
|---|---|---|
| E12 | U8（起草1行の事前提示）を初回実運用まで先送りせず、既存 correction 5件で表示サンプルを実際に作り事前評価する | **§11-A（新設）**。実データ5件でサンプルを作成し実測。当初の「400字超なら起草行を削る」という合格条件は誤りと判明（起草行はコストの主因でない・固定文言が主因）。§4.3 の detail 文言を tacchi Must-1 の情報量を保ったまま簡潔化（793字→635字）。それでも400字は超えるため、「定常（2日目以降）読み取りコストのみを400字基準の対象とする」という運用解釈に切り替えた。**（3巡目で撤回・下記参照）** |

### tacchi（利用者・実態突合の視点）— 2巡目 2026-08-17・「方向承認・修正要求つき」・[Must]4件 + [Should]3件 + [Nit]3件 → **反映済み**

1巡目の反映（4択化・実在確認）は正しかったが、**実際に画面に出る文言そのものに欠陥が残っていた**
という指摘。核心:

> § 番号や内部語をそのまま利用者の画面に出している。Other に detail が付くという前提が
> 自分の実測（U1）と矛盾している。

| # | 指摘 | 反映先 | 反映内容 |
|---|---|---|---|
| Must-1 | §4.3 の detail に § 番号・内部語（「回しません」等）が「そのまま出す」対象として残っている | **§4.3** | detail 文言を全面書き換え。§ 番号・file:line・内部語を一切含まない4行に整理。字数も併せて削減（357〜377字/問） |
| Must-2 | Other は tool が自動付与するプレーンな選択肢で、カスタム detail を持てない（自分の U1 実測と矛盾） | **§4.3・§4.4（新設）** | options 表から Other 行を削除。memory/pitfall の誘導は question 本文末尾の1行へ、skill/hook の応答は「Other に書かれた**後**」の応答として §4.4 に分離。skill/hook とも「回します」でなく実行可能な次の一手（`/evolve-anything:evolve-skill` / `hooks/` 手編集）を案内するよう §5 の該当行も修正 |
| Must-3 | §11-A の「定常時のみ400字」という再解釈を削除し、簡潔化した文言で再実測すべき | **§4.3・§11-A** | Must-1/2 の書き換え後に再実測した結果、平均357字・最大377字で detail 込みでも400字以内に収まった。「定常時のみ」の運用解釈は撤回し、通常どおり1問あたりの実表示で判定する方式に戻した |
| Must-4 | 「あとで戻せます（同ファイルへの後続変更が無い場合）」は選択時点で評価不能（対象ファイル未確定） | **§4.3.1（新設）** | 選択時点の detail は「取り消せます（条件は反映時に表示）」に簡略化。精密な取り消し条件は反映完了メッセージ（`bin/evolve-revert <entry_id>` の案内込み）へ移動 |

**[Should]3件**:

| # | 指摘 | 反映先 | 反映内容 |
|---|---|---|---|
| Should-1（C-1） | 新規ファイル判明時の追加確認を y/n でなく3択に | **§4.3.2** | ①新しく作る（取り消し不可）②既存の候補ファイルに追記する（取り消し可・既定推奨）③やめる、の3択に変更。グローバル rules が既に33ファイルあることを根拠に②を既定候補に |
| Should-2（C-2） | 設問を出す前に §6.2 の一致判定で既反映を検出し、設問枠を消費しない | **§4.5（新設）** | §6.1 の実在確認関数を「書いた後」でなく「書く前」にも流用。一致すれば「既に反映済みでした」と1行報告して設問をスキップ。新規実装ほぼゼロ |
| Should-3（C-3） | 過去に「はい」した167件（P19の3件含む）が1行も反映されていない事実への移行節が無い | **§4.6（新設）** | 実装時1回限りの移行バッチで対象全件を `applied`→`promoted` に一括変更し、既存の §5.1 メカニズム（5件で `/reflect` 提案）へ乗せる。新しい通知経路・新しいストアは作らない |

**[Nit]3件**:

| # | 指摘 | 反映先 | 反映内容 |
|---|---|---|---|
| Nit-1 | label「全PJで毎回効く」の「毎回」が冗長 | **§4.3** | Must-1 の書き換えで「全PJで効く」に短縮済み |
| Nit-2 | detail の `<file>.md` 未解決プレースホルダが混乱の元 | **§4.3** | Must-1 の書き換えでファイルパスの雛形自体を detail から削除（完了メッセージ側でのみ実際のパスを出す・§4.3.1） |
| Nit-3 | 最悪ケースの朝は「5問+新規ファイル確認+Other聞き返し」で7問になりうる。頻度は未観測 | **§11（U10 新設）** | 「通常の設問数には数えない」は帳簿上の整理であり体感時間が増えないことは意味しないと明記。発生頻度を実装後の観測項目に追加 |

### codex（正しさの視点）— 3巡目 2026-08-17・判定 `設計修正要`・[Must]4件 → **反映済み**

tacchi 2巡目と結論が一致（「§11-A の『定常時のみ400字』という再解釈は不要。文言を直せば
当初条件をそのまま満たせる」）。

| # | 指摘 | 反映先 | 反映内容 |
|---|---|---|---|
| B-1 | 「5問30秒」は文字数しか測っておらず所要時間を測っていない。「2日目以降は読み飛ばす」を設計の依拠前提にしない | **§11-A・§11（U9 新設）** | ラベルのみ/detail全文それぞれの5問合計字数と、読字速度からの**推測**所要時間（合否判定には使わない）を併記。設計の合否は測れるもの（1問あたりの字数・400字以内）だけに置く。読み飛ばし前提は §11 U9 の実装後観測項目へ切り出した |
| B-2 | §7・§13 が `FROZEN_STORES` を「44件・±0」としているが、§12 決定4 で `optimize_history/<slug>.jsonl` を追加するため実装後は45件・+1 | **§7・§13** | 「+1（既存ストアの登録漏れ追認・新設ではない）」に訂正。前例（`shrink_freeze.py:55-60`）への参照を追加し、検証コマンドが「追加実装後」に緑であることを受入条件として明記 |
| B-3 | U8 の実測 artifact に、再現可能な実際のコマンド・入力・5件個別の出力が無い | **artifacts/475-measurement-log-20260817.md** | 実行コマンド全文・5件それぞれの `body_only`/`body_labels`/`full` 個別値・1件分の完全なテキストを追記。第三者が同じスクリプトで再計算できる形に |
| B-4 | 実測で裏取りされていない前提3つ（読み飛ばし・5問30秒・5サンプルの字数） | **§11-A・§11** | 読み飛ばし・30秒は B-1 のとおり依拠前提から除外し観測項目化。5サンプルの字数は Must-1/2 反映後の文言で再実測（B-3 の artifact に verbatim 出力あり） |

**この反映で増えた/変わった記録**: なし（新しい未確認事項は生まれていない。U2/U8 とも本巡で確定・実測済みに移行した）。
