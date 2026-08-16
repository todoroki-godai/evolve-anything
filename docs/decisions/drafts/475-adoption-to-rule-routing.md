# 475 設計ドラフト: 朝の y/n で採用した指摘を反映先まで一度に決める

- 対象 issue: **#475**（朝の y/n で「はい」した指摘がルール文書に反映される工程に届かない）
- 関連: #467（Epic・入口側）/ #379（縮小方針・新設凍結）/ #402・ADR-053（revert lane）/ #99（昇格チャネルの非対称）
- 状態: **設計ドラフト rev1（未レビュー）**。実装着手前。コードは1行も変更していない
- 前提コミット: `556d846d`（`docs/467-rev5-design-rollback`）
- 測定日: **2026-08-16**

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
以下はそのまま貼れば再実行できる。`#475` の [実測] 値はすべてこの3コマンドの出力である。

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

**[コード] 側の再現**: 凍結定数の現況は下記1行で出る（読み取りのみ）。

```bash
python3 -c "import sys;sys.path.insert(0,'scripts/lib');import shrink_freeze as s;print(s.SHRINK_FREEZE_ACTIVE,len(s.FROZEN_STORES),len(s.FROZEN_OBSERVABILITY_SECTIONS),len(s.FROZEN_ADVISORY_PROPOSAL_ADAPTERS),len(s.FROZEN_WEAK_SIGNAL_CHANNELS))"
```

---

## 2. この設計が依拠する前提と evidence（空欄なし）

計 **20 件**（[コード] 12 / [実測] 5 / 測定不能 3）。

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
| P17 | corrections は全 **172 件**。`reflect_status` は `applied 163 / skipped 8 / pending 1`。`source` は `reflect_confirmed 162 / backfill 8 / hook 2`。**`reflect_confirmed` は 162 件すべてが `applied`** | [実測] | M1 / 2026-08-16 |
| P18 | `reflect_confirmed` の日付分布は 2026-06-12 の 41 件が最多。最古 **65 日前**・最新 0 日前。**90 日超は現在 0 件**（＝まだ1件も prune されていない） | [実測] | M2 / 2026-08-16 |
| P19 | 2026-08-15 に朝の y/n で promote されたのは **3 件**。内容は ①「並行して作業できないの？」②「spec-keeper は codex レビューいらない」③「memory だとこの PJ だけじゃないの？ rule にした方がよくない？」。3件とも `routing_hint: None` / `extracted_learning: None` | [実測] | M1 の変形（`timestamp[:10] == "2026-08-15"` で絞る）/ 2026-08-16 |
| P20 | fleet 全体の採用履歴は **42 件**、うち **戻せるもの（`revert_schema_version` あり）は 1 件だけ**（`skills/spec-keeper/SKILL.md`・scope=project・2026-08-15） | [実測] | M3 / 2026-08-16 |

### 2.5 測定不能（空欄にしない）

| # | 事項 | 理由 |
|---|---|---|
| U1 | `AskUserQuestion` の選択肢上限が 2〜4 であること | **測定不能（本セッション）**。当該 tool が本セッションに提供されておらず、実行して境界を確かめられない。issue #475 コメント（2026-08-15 ユーザー）の主張をそのまま前提に採る。実装時に1回叩いて確定させること |
| U2 | 「Other」（自由記述）から残り4反映先へ確実に到達できること | **測定不能（本セッション）**。U1 と同じ理由。設計上は Other を唯一の逃げ道にしているので、実装時の実測が必須 |
| U3 | ルール文書に書いた指摘が実際に AI の振る舞いを変えたか | **測定不能（現時点）**。因果判定 (b) は revert 済みを畳んだ有効 accept 件数が分母で、`bin/evolve-audit --growth` が `not_measured` を返す段階（ADR-054 §5・§7.2）。**本設計はこれを解決しない**（届くようにするだけ） |

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

### 4.2 選択肢の並べ方（8 反映先 → 1 問に収める）

反映先は issue コメントで **6 種**（グローバル rule / PJ rule / skill / hook / memory / pitfall）に確定。
これに「記録のみ」「いいえ」を足して 8。`AskUserQuestion` は1問4択が上限（U1）なので**全部は並ばない**。

**採る方式**: 指摘の内容から**反映先候補を2つに絞って先頭に置き**、続けて「記録のみ」「いいえ」を置く。
残り4反映先へは **Other（自由記述）** で到達する。

- 絞り込みは既存の `suggest_claude_file`（`scripts/lib/reflect_routing.py:108`）の**並べ替えとしてのみ**使う。
  決めるのは常に人間（issue 必須要件4に非抵触）。
- ただし P6 のとおり同関数は**グローバル rule を候補として返さない**。並べ替えに使う前に、
  返り値を反映先ラベル（6種）へ写す薄い対応表が要る（`~/.claude/CLAUDE.md` → 「共通ルール」に写す等）。
  これは新しい検出器ではなく**既存関数の出力の読み替え**。

### 4.3 選択肢の文言（実際に出す日本語）

question 本文（既存の提示材料はそのまま）:

```
「{idiom または representative}」（{count}回・{他PJ承認済みなら「他PJ（slug…）で承認済み」}）
この指摘を、どこに反映しますか？
```

options（label / detail をそのまま出す）:

| # | label | detail（必ず添える） |
|---|---|---|
| 1 | **共通ルールに書く（全PJで毎回効く）** | `~/.claude/rules/<file>.md` に追記します。次のセッションから**全プロジェクトで**指示として読まれます。全PJに効くので影響範囲は最大です。 |
| 2 | **このPJのルールに書く（このPJだけ毎回効く）** | `<このリポジトリ>/.claude/rules/<file>.md` に追記します。次のセッションから**このプロジェクトでだけ**指示として読まれます。 |
| 3 | **いまは反映しない（記録だけ残す）** | **AI の振る舞いは変わりません。** 記録は棚卸しの件数と他PJでの優先提示にしか使われず、**90日経つと自動削除されます**。あとで反映したくなったら、朝の確認でもう一度出せます。 |
| 4 | **いいえ（この指摘は不要）** | 記録も反映もしません。次回から出しません。 |
| — | Other（自動付与） | skill / hook / memory / pitfall に書きたいときはここに書いてください（例: 「pitfall に書く」「memory に残す」）。 |

**1b への対応（保留であることが読み取れる文言）**: 3 の label から「記録のみ」という中立語を外し、
**「いまは反映しない」**を先頭に置いた。detail の1文目を「AI の振る舞いは変わりません」にして、
効果がないことを最初に読ませる。さらに **90日で消える**（§3.4(b) の実測に基づく事実）を書く。
2026-08-15 に使われた「恒久ルールとして残す」という説明は、この文言では成立しない。

**選択肢1・2の順序**: `suggest_claude_file` の並べ替え結果で 1 と 2 が入れ替わる。
skill / hook / memory / pitfall が上位に来た場合は、その反映先が 1 または 2 の位置に入り、
押し出された rule は Other へ回る（Other から全反映先に到達できることが前提・U2）。

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
| skill（Other） | 該当 SKILL.md | **既存の skill 採用レーンへ回す**（`matched_skills` / `skill_evolve`）。この場で直接書かない | 既存レーン | 既存の y/n |
| memory（Other） | `~/.claude/projects/<enc>/memory/` | `auto_memory_broker` の既存経路（project スコープ4層防御 + `memory_guard`） | 既存経路 | 同上 |
| pitfall（Other） | `references/pitfalls.md` | **手で markdown を編集しない**。`pitfall-curate` 経由（#471） | `pitfall-curate` | 同上 |
| hook（Other） | 新規 hook ファイル | **戻せない**（P15）。実行前に「この反映は戻せません」と明示して再確認する | agent | 同上 + 追加確認 |

**「いまは反映しない」を選んだ指摘の再提示**: `record_reviewed` は呼ばない（既読にしない）ので、
次の朝にもう一度出る。これが文言 3 の「あとで反映したくなったら、朝の確認でもう一度出せます」の実体。
毎朝同じものが出るのが煩わしい場合の抑制は §12 のユーザー判断2に置いた。

---

## 6. `reflect_status` の意味を分ける（issue 必須要件3・受入条件4）

現状は「昇格済み（人間が"はい"と言った）」と「反映済み（文書に書かれた）」がどちらも `applied`。
この設計を入れても両者を区別しないと、**同じ取りこぼしが別の形で再発する**（issue 必須要件3）。

**採る案**: `reflect_status` の値域を3つにする。**新しいフィールドもストアも増やさない。**

| 値 | 意味 | 誰が付けるか |
|---|---|---|
| `pending` | 未判断 | hook（`hooks/correction_detect.py:144`） |
| `promoted` | **昇格済み・反映先は未定**（＝「いまは反映しない」を選んだ） | `promote.py` |
| `applied` | **反映済み**（どこかの文書に実際に書かれた） | 反映を完了した時点で `promote.py` / reflect の既存更新経路 |

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

**壊して赤くする検査**（`~/.claude/rules/verify-checks-by-breaking.md`）: 契約テストは
**2方向**で赤くなること — ①`promote.py` が `applied` を書くように戻したら赤 /
②`suppression.py` の条件を `!= "applied"` に緩めて `promoted` を拾わせても赤（片方向だけだと
「値が増えたことだけ数える」テストが素通りする）。

---

## 7. #379 新設凍結との整合（受入条件2）

**新しい store / observability section / advisory proposal adapter / weak_signal channel を1つも増やさない。**
`scripts/lib/shrink_freeze.py` を実際に読んで確認した。

| 凍結対象 | 現況 [コード] | 本設計での増減 |
|---|---|---|
| `FROZEN_STORES` | 44 件（`corrections.jsonl` / `correction_review_seen.jsonl` / `correction_idioms.jsonl` を含む） | **±0**。書込み先は全部この 44 に含まれる既存ストア |
| `FROZEN_OBSERVABILITY_SECTIONS` | 44 件（うち 32 件は `CULLED_OBSERVABILITY_SECTIONS` で表示淘汰中） | **±0**。新しい section を作らない |
| `FROZEN_ADVISORY_PROPOSAL_ADAPTERS` | 2 件（`invalid_frontmatter` / `testpaths_coverage`） | **±0**。adapter を足さない |
| `FROZEN_WEAK_SIGNAL_CHANNELS` | 6 件（`esc_interrupt` / `llm_judge` / `manual_edit_after_ai` / `permission_deny` / `rephrase` / `verbosity`） | **±0**。既存 channel の中身に手を入れない |

- 凍結は有効（`SHRINK_FREEZE_ACTIVE: bool = True`・`shrink_freeze.py:44`）。
- 増やすのは **既存ストアのフィールド値**（`reflect_status` の新値 `promoted`、`record_reviewed` の
  `decision` 文字列）だけ。`assert_no_new_keys`（`shrink_freeze.py:259-275`）はストア名・section 名・
  adapter 名・channel 名を見るので、**フィールド値の追加は検出対象外＝違反にならない**。
- 検証コマンド（実行済み・2026-08-16）: `python3 -m pytest scripts/lib/tests/test_shrink_freeze.py -q -n 0`
  → **13 passed**。実装後も同じコマンドが緑であることを受入条件の証拠にする。
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
| **やらないこと** | **新規ファイル作成の revert**（before 本文が存在しないため「不在」sentinel と schema version 2 が要る。#467 §1.4 が同じ穴を指摘済み） | ここは実装しない |

**帰結**: 新規 rule ファイルを作る反映と、Other 経由の hook 反映は **戻せない**。
受入条件は「1コマンドで戻せる、**または戻せない場合はその制約を明示する**」なので、
**選択時に「この反映は戻せません」と明示して再確認する**ことで満たす（§5 の hook 行と同じ扱い）。

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
| 反映先の並べ替え | `scripts/lib/reflect_routing.py:108`（`suggest_claude_file`） | 候補の**並べ替えにのみ**使う。返り値→反映先ラベルの写像は薄い対応表で足す（P6 の欠落を埋める） |
| 昇格 CLI | `evolve-reflect --promote-weak`（`correction-review.md:65`） | そのまま使う。`reflect_status` の値だけ §6 に従って分岐させる |
| 既読記録 | `correction_semantic/daily_review.py:111`（`record_reviewed`） | `decision` に反映先を入れて呼ぶ（自由文字列・P9）。**部分失敗時は `promoted_keys` のみ渡す既存 MUST を維持**（#326） |

**新しい工程を作る必要があるか**: 無い。反映の手順（Edit の作法・行数上限・新規/既存の分岐）は
reflect スキルが既に全部持っている（P5）。本設計がやるのは
**「その手順を朝の設問の直後に呼ぶ」ことと「promote が入力集合から外さないようにする」ことだけ**。

---

## 10. 失敗モードと、どう気づくか（気づけないものは正直に書く）

| # | 失敗モード | 気づけるか | 検出方法 |
|---|---|---|---|
| F1 | agent が SKILL.md の4択手順を飛ばし、従来どおり y/n だけ出す | **気づける** | 決定論 proxy: `record_reviewed` の `decision` 分布。反映先つきの値が1件も出ないなら手順が発火していない。`learning_skill_md_must_not_enforcement` の既知パターン |
| F2 | 4択は出たが、いつも「いまは反映しない」が選ばれる | **区別できない（設計の限界）** | F1 と同じ分布を見ても「手順が出ていない」と「出たが毎回3を選んだ」は同じ形にならない（F1 は reflect_status が `applied` のまま、F2 は `promoted`）ので**区別はできる**が、「なぜ3ばかりなのか」は測れない。文言が悪いのかユーザーの意思なのかは**気づけない** |
| F3 | rule ファイルに書いたが 10 行上限を超えて壊れる | **気づける** | 既存 `check_line_limit`（`line_limit.py:81`）+ `reflect.py:253-262` の `line_limit_warning` |
| F4 | 反映先を間違えた（PJ 固有の話を共通ルールに書いた） | **気づける（戻せる）** | §8 の revert。`bin/evolve-revert --list` に出る |
| F5 | 新規 rule ファイルを作る反映をして、戻せないことに後で気づく | **気づける（事前に）** | 選択時に「この反映は戻せません」を出す（§5・§8.2） |
| F6 | `reflect_status` に新値を入れたことで、洗い出せていない reader が壊れる | **部分的にしか気づけない** | §6 の表は `grep -rn reflect_status scripts/ hooks/ skills/ bin/` の全件（tests 除く）に基づく。**tests 配下と外部ツールは見ていない**ので取りこぼしうる。フルスイート（`python3 -m pytest`）が最後の網 |
| F7 | 反映したルールが実際には行動を変えていない | **気づけない** | U3。因果測定 (b) は分母が揃っておらず `not_measured`。本設計はここを解決しない。**「届いた」ことしか測れず「効いた」ことは測れない**と明記しておく |
| F8 | 「いまは反映しない」が毎朝再提示されて承認疲れを起こす | **気づける** | 同一 `signal_key` の提示回数。ただし現状カウンタは無い（既読にしないので記録が残らない）。§12-2 のユーザー判断次第 |

---

## 11. 未確認事項（空欄にしない）

| # | 事項 | 状態 | 何が変わりうるか |
|---|---|---|---|
| U1 | `AskUserQuestion` の選択肢上限（2〜4） | **測定不能（本セッション）**。当該 tool 未提供。issue コメントの主張を採用 | 上限が違えば §4.2 の「2候補 + 記録のみ + いいえ + Other」の配分が変わる。実装時に1回叩いて確定させること |
| U2 | Other（自由記述）から skill / hook / memory / pitfall へ確実に到達できるか | **未実測**（U1 と同根） | 到達できないなら 8 反映先を1問に収める前提が崩れ、2段階設問の再検討が必要になる |
| U3 | 反映したルールが行動を変えたか | **測定不能（現時点）** | ADR-054 の (b) が計測可能になるまで判定不能 |
| U4 | `suggest_claude_file` の並べ替え精度（上位2件に正解が入る率） | **未実測**。実データでの当たり率を測っていない | 低ければ Other 経由が常態化し「1問で決まる」体験が成立しない。実装前に既存 162 件の `reflect_confirmed` に対して dry-run 較正すること（LLM 不要・決定論） |
| U5 | 他 PJ のストアでも同じ分布か | **未実測**。[実測] 値はすべてこのマシン 1 台 | 他環境では件数が違う。ただし §3.2 の「0 件」は構造証明なので環境に依存しない |
| U6 | `optimize_history` が `store_registry` 未登録であることの是非 | **未確認（スコープ外）** | #434 の事前契約ゲートの論点。本設計は既存形式に entry を足すだけなので判断を先送りできる |

---

## 12. ユーザー判断が要る点

### 判断1: 4つ目の選択肢を「いいえ」にするか、反映先候補を3つにするか

- **推奨: 「いいえ」を残す（反映先候補は2つ）。**
- 理由: 不要な指摘を却下する手段が無くなると、同じものが毎朝出続ける。
  `record_reviewed(decision="rejected")` は既存機能（P9）なので、捨てるのはもったいない。
- 選ばなかった場合（反映先3つ + 記録のみ）: 反映先の当たり率は上がるが、
  却下は Skip（＝次回再提示）でしか表現できず、**不要な指摘が毎朝出続ける**。
  承認疲れは1日5件の上限（P8）では吸収しきれない。

### 判断2: 「いまは反映しない」を選んだ記録を、90日で消すか残すか

- **推奨: 消さない**（`prune/corrections.py:84` の削除対象集合に `promoted` を**入れない**）。
- 理由: 選択肢の文言で「あとで反映できます」と約束するのに、90日で黙って消えるのは
  issue 必須要件 1b（実態を偽らない）に反する。
- 選ばなかった場合（消す）: `corrections.jsonl` は増えないが、
  「あとで判断」が予告なく消える。**現状 2026-09-10 前後に 41 件が消える見込み**（§3.4(b)）。
- 消さない場合の副作用: `corrections.jsonl` が単調増加する。現在 **172 行 / 220 KB**（[実測] 2026-08-16）
  なので、当面（数年規模）問題にならない。

### 判断3: rule 反映の revert 対応をどこまでやるか

- **推奨: 既存 rule ファイルへの追記のみ対応（§8.2）。新規ファイル作成は「戻せない」と明示する。**
- 理由: 既存ファイル追記なら before 本文が存在するので、現行 revert の3分岐
  （normal / 冪等 / conflict）がそのまま使える。追加は root 解決の分岐2本と定数1行だけ。
- 選ばなかった場合（フル対応）: 「不在」sentinel + `revert_schema_version` 2 の導入が要る。
  これは #467 §1.4 が hook 提案について指摘した穴と**同じ穴**なので、
  #467 と合わせて1回で塞ぐ選択肢もある（その場合は本 issue のスコープを超える）。
- 選ばなかった場合（revert 対応しない）: 受入条件3は「制約を明示する」で形式的には満たせるが、
  **全PJに効く共通ルールを戻せない**のは影響範囲的に危うい（issue 必須要件2 の指摘どおり）。

---

## 13. 受入条件との対応

| 受入条件 | 本設計での担保 | 証拠 |
|---|---|---|
| 朝の y/n で「はい」を選んだ項目が、同じ設問の中で反映先まで決まる | §4.3 の1問4択 + Other | 実装後に `record_reviewed` の `decision` 分布で確認 |
| 新しい store / observability section / advisory adapter / weak_signal channel を1つも追加しない | §7（4定数すべて ±0） | `python3 -m pytest scripts/lib/tests/test_shrink_freeze.py -q -n 0` → 13 passed（2026-08-16 実行済み・実装後も同じ） |
| 反映したルール行を1コマンドで戻せる（戻せない場合は制約を明示） | §8.2（既存ファイル追記は戻せる / 新規作成・hook は明示して不可） | `bin/evolve-revert --list` に rule entry が出ること |
| 「昇格済み」と「反映済み」が別状態として区別される | §6（`promoted` / `applied`） | §6 の2方向 mutation テストが赤くなること |
