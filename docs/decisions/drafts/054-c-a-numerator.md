# ADR-054 §7.2 — 柱3(a)「手直しの減少」の分子・分母の確定

対象: ADR-054 §7.2 の未決「A0 修理後の『手直し』分子の最終形」。
前提: G1 計測ゲートは 2026-08-13 に PASS（`llm_judge` レーンの recall 80.0% / CI 下限 49.0% > hook レーン 4.5%）。

**rev2（2026-08-13）**: codex / tacchi 各1巡 + 頭の追実測を反映。
v1 の推奨のうち **(1-A) バックフィルと カバレッジ30%ゲートは両レビュアー一致で却下**され、
設計は「**前向き専用 + 全量判定週のみ + 確定後 freeze**」へ変わった。反映表は §6。

---

## 1. 実測（すべて 2026-08-13・実データ）

### 1.1 各ストアが持っている時刻の意味

| ストア | 件数 | 時刻 | その時刻の意味 |
|---|---|---|---|
| `correction_judged.jsonl` | 全 3,816行（うち `key` を持つ 3,802件 / `key` 無しの課金記録 14行） | `judged_at`（**84% 欠損**・3,204/3,816） | judge を回した時刻 |
| `weak_signals.jsonl`（`channel=llm_judge`） | 351件 | `detected_at` | **judge を回した時刻**（発話時刻ではない） |
| `utterances.db` | dialogue 10,516件（sidechain 除外後） | `timestamp`（VARCHAR NOT NULL・欠損なし） | **発話が起きた時刻** |

`weak_signals.provenance.{source_path,line_no}` は `correction_judged.key` と完全に突合する
（TP 351件が judged 3,802件の部分集合）。`utterances.db` の PK とも一致する。

### 1.2 `detected_at` は系列に使えない（§2.1 と同型の罠）

`llm_judge` の `detected_at` 日別: **2026-06-10 に 313件**（一括バックフィル）、08-11 に 16件、08-13 に 15件、08-12 に 7件。
`detected_at` で週を切ると「judge を回した日のログ」になり、§2.1 で却下した
「corrections は reflect を回した日のログ」と同じ誤りになる。

### 1.3 発話時刻で週を切った実測（全 PJ・dialogue・sidechain 除外）

| 週 | 発話数 | judge 判定済 | TP | カバレッジ | 判定済内の陽性率 |
|---|---|---|---|---|---|
| 2026-W24 | 603 | 330 | 68 | 54.7% | 20.6% |
| W25〜W31 | 6,335 | **0** | 0 | **0.0%** | 測定不能 |
| 2026-W32 | 975 | 129 | 6 | 13.2% | 4.7% |
| 2026-W33（進行中） | 385 | 269 | 10 | 69.9% | 3.7% |

W24 の 20.6% と W33 の 3.7% は**判定母集団の選ばれ方が違う**（前者は過去分の一括バックフィル、
後者は daily の newest-first）ので、同じ折れ線に並べてはいけない。

### 1.4 daily runner 稼働後は新規発話が当日〜翌日に100%判定されている【rev2 で追加・設計の土台】

judge 実行日別の判定件数: **08-11=200 / 08-12=207 / 08-13=205**（日次上限200に張り付き＝backlog 消化中）。

発話日ベースのカバレッジ:

| 発話日 | 発話数 | 判定済 | カバレッジ |
|---|---|---|---|
| 08-08 | 13 | 13 | **100.0%** |
| 08-09 | 60 | 60 | **100.0%** |
| 08-10 | 78 | 78 | **100.0%** |
| 08-11 | 73 | 73 | **100.0%** |
| 08-12 | 234 | 118 | 50.4%（翌日枠で回収中） |

`select_daily_batch` は newest-first なので**新規発話が最優先**され、backlog（在庫 10,419件）は
余った枠で少しずつ消化される。日次発話量は median 108 / p90 234 / max 718（直近60日）、
200超の日は 21日中4日。**週合計 385〜1,566件 に対し週上限は 200×7=1,400件**なので、
平常週は週内に全量判定が完了する。

→ **「週内100%判定」は現行設定のままで達成可能**（W25〜W31 が0%なのは daily runner 稼働前だから）。

### 1.5 `promoted`（朝の y/n 通過）は分子にできない

`llm_judge` TP 351件のうち `promoted=true` は 123件（35.0%）。全 weak_signals 1,188件では 149件（12.5%）。
分子にすると測るのは手直し量でなく**ユーザーが朝レビューをどれだけ消化したか**。§2.1 の再演なので却下。

### 1.6 分母の偏りに関する追加実測

- `judge_runner.select_daily_batch`（102-125行）の選択は **`timestamp` 順のみで発話内容に依存しない**。
  内容フィルタは無い。切り詰めはトークン上限による末尾（古い側）カットのみ。
- machinery 除外は `utterance_archive/extractor.py:290` の `_is_harness` で **`utterances.db` へ入る前**に行われる。
  `correction_semantic/` 側に machinery 除外は無い（grep 済み）。→ §1.3 の分母は既に machinery 除外後。
- `source_kind` 分布: `dialogue` 13,471 / `long_paste` 4,074 / `excluded_pj` 1,368。分母は `dialogue` のみ。

---

## 2. 確定（rev2）

### 2.1 指標の定義

| 項目 | 定義 |
|---|---|
| **名称** | **指摘率**（correction rate）。「手直し率」と呼ばない（承認行為と無関係だと読める語にする・tacchi） |
| **週の切り方** | `utterances.timestamp`（発話の実時刻）。TZ は **UTC 固定**、ISO 週 |
| **分母** | その週の発話のうち judge が判定した件数。`COUNT(DISTINCT physical_key)` |
| **分子** | そのうち judge が TP と判定した件数（`weak_signals` `channel=llm_judge`・`promoted` 不問）。`COUNT(DISTINCT physical_key)` |
| **母集団** | `source_kind='dialogue'` かつ `source_path NOT LIKE '%/subagents/%'` |
| **表示条件** | **カバレッジ 100%（未判定 0件）の確定週のみ**。未満は値を出さない |
| **確定週** | 週の終了後 D=3 日時点。**進行中の週は表示しない** |

### 2.2 freeze — 過去の値は後から動かさない【tacchi [Must]・最重要】

B3 で backlog を drain した瞬間、あるいは A1 第二段階 migration 後の再判定で、
**空洞週が「後から」埋まり過去の系列が黙って変わる**。これが #376 再発の最短経路。

**対策（新ストアを作らずに read 時導出で実現する）**:
週 W の cutoff を **`W 終了 + D 日`** と定義し、**3ストアすべてに同じ cutoff を課す**
（rev3・codex [Must]1: 分子だけ freeze しても**分母が動けば率は動く**）:

| ストア | cutoff 条件 | これが無いと |
|---|---|---|
| `utterances.db`（分母） | **`ingested_at <= cutoff`** | 遅延 ingest / 再 ingest / migration で確定後に分母が増える |
| `correction_judged.jsonl`（判定） | `judged_at <= cutoff` | backlog drain / 再判定で確定後に判定済が増える |
| `weak_signals.jsonl`（分子） | `detected_at <= cutoff` | 再検出で確定後に TP が増える |

**確定後は対象行を更新・削除しない**ことを契約とする（`judged_at` の書換え禁止を含む）。

**競合解決**（rev3・codex [Must]2。migration / 手修復 / union read で同一 key に複数記録が生じ得る）:
- 同一 physical key に複数の有効 `judged_at` がある → **最古の有効判定を採用**
- 同一 key に**相反する TP 記録**がある → その週は**集計失敗として非表示**（値を出したまま揺れさせない）
- raw TP の削除・物理削除を検出 → その週は**系列を出さない**
- 単なる「件数 surface」では値を表示したまま変動する経路を閉じられないので、上記は表示可否そのものを左右する

**D の値**: 実測（§1.4）から D=3 を初期値とするが、**仮の運用値**（codex [Should]）。
週最大 1,566件は週上限 1,400件を超えるため、トークン上限・失敗・翌週の新規発話を newest-first で
優先する影響は未評価。連続週の「週終了 → 100%到達」の実測 p90/p95 が取れ次第 D を再設定する。
100%表示ゲートがあるため **D の誤差は誤った率でなく「未測定週の増加」として現れる**（安全側に倒れる）。

**帰結**: `judged_at` を持たない判定（現存 3,204件・全て daily runner 稼働前）は系列に使えない。
→ **過去週は構造的に永久 `not_measured`**。これは §2.3 の「前向き専用」と自動的に整合する。
なお欠損レコードも**再判定防止の key 集合としては使われ続ける**ため、該当発話は再判定されず
**永久に系列へ入らない**（codex [Should]）。系列開始日**以後**の発話に `judged_at` 欠損が生じた場合は、
過去扱いせず **producer / schema 異常としてその週を未測定にする**。

### 2.3 前向き専用 — バックフィルしない【codex [Must]4 / tacchi 一致】

W25〜W31 の空洞は埋めない。理由:
- 埋めても `prev_action` null + 現行 prompt での判定＝**発話時と別条件の測定**になり、
  §1.3 で自ら却下した「W24 と W33 を同じ折れ線に並べる」誤りを自分の手で再生産する
- 過去週に対してユーザーが取れる行動は無い（$2.6 で買うのは設計者の「系列の綺麗さ」）
- 比較可能な過去系列を作るなら W24〜W33 の**全対象**を同一条件で再判定する別系列にする必要がある（穴だけの補填は不可）

**空洞週は `not_measured` 行すら出さない**。系列の開始点は「最初の全量判定確定週」（tacchi）。
10週中7週が「データなし」の画面を毎週見せるのは柱3の逆効果。

### 2.4 集計契約【codex [Must]7】

- 分母・分子とも `COUNT(DISTINCT physical_key)`
- **実行時検証**（rev3・codex [Must]3。「分子 ⊆ 分母」だけでは orphan TP による水増ししか防げず、
  分母の遅延増加・TP の欠落削除・cutoff 外判定との誤結合・重複判定の競合は守れない）:
  - 分母: `utterances.timestamp ∈ W` **かつ** `ingested_at <= cutoff`
  - 判定: 同じ physical key **かつ** 有効な `judged_at <= cutoff`
  - 分子: 上記判定に対応し、**TP 記録自体も cutoff 内**
  - 分子 ⊆ 分母
  - **不整合・競合・削除を検知したら当該週を表示せず明示エラー**にする（黙って値を出さない）
- `correction_judged.jsonl` の `key` 欠損14行（課金記録）は分母から除外し、**除外件数を surface**する
- `weak_signals` は promoted / TTL / 既読を問わない **raw 記録**を読む（失効フィルタ付き reader は使わない）
- **key 文字列を末尾の `:` で分解して join しない**（`source_path` にコロンが入り得る）。
  DB 側で同じ `f"{source_path}:{line_no}"` を構成するか、構造化された2列で突合する
- orphan / 重複 / 欠損 / 型不一致 / archive stalenessは**件数として surface**し、黙って除外しない

### 2.5 provenance と系列の分割【codex [Must]5 / tacchi】

`evaluation_provenance` を適用する。ただし**過去レコードから条件は復元できない**
（`correction_judged` は key と `judged_at` のみ、weak_signal の provenance は `judge="llm_haiku"` 程度）。
→ **過去は `unknown provenance`。将来分は producer 時点で付与**する（集計時に現在値を付けるのは
「過去を現在条件で測ったように見せる」三度目の同型事故）。

系列は **同一 harness の連続区間ごとに分ける**。区切りになる条件:
model / prompt fingerprint / **extractor_version**（extractor 側フィルタの変更は分母自体を変える）/
`prev_action` 利用条件 / 選択方式 / 主要 limit / 週 TZ。

### 2.6 「見かけの改善」経路 — 断絶または `not_measured` にする【codex [Must]6】

model alias の指す実モデル・model・effort・tool policy の変更 / system・batch prompt・判定ラベル・
parser・切り詰め長・batch size の変更 / extractor_version・`prev_action` 充填率・machinery/sidechain
除外の変更 / 日次件数上限・トークン上限・実行頻度・停止日・失敗率・部分応答率の変更 /
newest-first backlog 量と週内の判定位置の変化 / 発話長分布の変化によるトークン上限内の選択件数変化 /
archive ingest の遅延・停止・再 ingest・transcript 消失による分母変化 / weak_signal の TTL・read 時
失効フィルタ・cleanup による過去分子の減少 / 進行中の週を確定週と比較すること / ISO 週 TZ の不統一 /
**PJ 構成比の変化による Simpson のパラドックス** / 同一物理キーの重複 signal・欠損 key・型違いの `line_no` /
judge の非決定性と過去レコードを再判定しないことによる基準混在。

### 2.7 表示【tacchi [Must]】

- 表示先は **戦果ボード（`results_board`）**。新 observability section は作らない（#379 非抵触）
- **現行の rework 表示（`results_board.py:196` の `count_human_corrections`＝§2.6-2 で自ら
  「嘘をつく数字」と認定した数え方）は置換する。併存させない**（「手直し」を名乗る数字が2つ並ぶのは #376 の再演）
- 測定不能なら `指摘率: 未測定（判定カバレッジ X/Y）` と出す。**#508（2026-08-18）**: 全量判定の
  確定週が1件でもあれば「未測定」でなく、その1週分の率を点として出す（§2.9 参照。系列表示の
  規則ではない）
- **全 PJ 合算を主、PJ 別の判定済件数・TP 数・カバレッジを必須 evidence として併記**
  （Simpson 防御。1桁分母の PJ 別率は表示しない）
- precision 80% ＝ **分子の2割は誤りを含む**前提なので、率の絶対値を断定形で見せない文言にする
- **悪化週のみ、その週の TP 実発話 TOP3 を併記し朝レビューへ導線を付ける**（改善週は数字1行で終わり）。
  実感は「率を見る」ことでなく「自分が何を直させられたかを思い出す」ことから生まれる。
  新 channel・新提案レーンは作らず、既存 `llm_judge` の reason / idiom / PJ 内訳を証拠に使う

### 2.8 G1 の位置づけの訂正【codex [Must]3】

G1 は TP 10 / not_TP 10 の**均衡 corpus** での測定なので、実運用の低ベースレートにおける PPV を保証しない。
§2 の根拠を「G1 で recall 80% を実証済み」から
**「hook より捕捉性能が高いことは確認したが、母集団率の校正は未検証」**に改める。

### 2.9 実施順とゲート【tacchi 論点3】

**§6 の実施順は改訂しない**（B3 の扱いは §7.1 でユーザーが「A1/A2 後に再計測してから判断」と決定済みで、
実施順の改訂はその決定と衝突する）。代わりに **表示開始ゲート**で自然従属させる:

> **全量判定（カバレッジ100%）の確定週が k=4 週連続で揃うまで、指摘率の系列を表示しない。**

揃わない間は `指摘率: 未測定（判定カバレッジ X/Y）` のみ。
これにより B3 の結論（上限を上げるか対象を絞るか）が出ていなくても C(a) の実装は先行でき、
運用が安定しなければ数字は出ない。看板「週1の数字で実感」への復帰も、この k 週の実績が出てから。

**#508（2026-08-18・方針変更）**: ユーザー決定「9/9 まで待ちたくない。1週分から出す」を受け、
上記ゲートは**系列**表示のみを対象とすることに改める。全量判定の確定週が1件でもあれば、
「未測定」の代わりにその週の率を**点**（推移ではない・PJ 別内訳込み）として先に出す。
k=4 週連続ゲート自体・freeze 契約・分子分母の定義は変更しない。詳細設計は
[drafts/508-single-week-rate-point.md](508-single-week-rate-point.md)。

---

## 3. 実装スコープ

- **新ストアなし**（3ストアの read 時 join で導出。既存学習「派生系は read 時にログから導出」と一貫）
- **新 observability section なし**（既存 `results_board` に統合）
- 既存 `correction_judged.jsonl` / `weak_signals.jsonl` への **provenance フィールド追加は非抵触**（新 store / 新 channel でない）
- 性能: dialogue 10,516行 × judged 3,802 key × TP 351 key。JSONL 2本を set に読み DuckDB を1走査するだけ（懸念なし）

## 4. 残る前提の確認事項（進行に影響しない）

- 起点は daily runner が安定稼働した 2026-08-11 以降。最初の確定週は **W34 = 2026-08-17（月）〜 08-23（日）**、
  cutoff は **08-26**（週終了 + D=3）。**W33（08-10〜08-16）は 08-10 が daily runner 稼働前なので 100% に届かず対象外**
  （2026-08-13 訂正: 旧記述「W34（08-17 終了 + D=3 → 08-20 頃）」は ISO 週の取り違え。08-17 は W34 の**開始**日）
- `prev_action` の有無が recall/precision を変えるかの A/B は **A1 第二段階の解凍条件として独立に価値がある**が、
  **C(a) の前提にはしない**（tacchi）

## 5. 未解決（本設計の範囲外）

- A2 以前に検出済みの subagent 由来 weak_signal（TTL 45日＝2026-09-13 頃まで生存）が
  朝の y/n に出続け昇格し得る問題。実測: rephrase 6件の `detected_at` は全て 2026-07-30 で、
  うち4件が A2 マージの21分後（08-13T01:01）に corrections へ昇格した。
  → weak_signals 側の read 時除外（A1 で `query.py` に入れたものと同型）が要るかは別途判断

## 6. レビュー反映表（2026-08-13 rev2）

| # | 指摘 | 出所 | 反映 |
|---|---|---|---|
| 1 | 「判定済み標本の陽性率」を「手直し率」に昇格している | codex [Must]1 | §2.1 で名称を**指摘率**に変更、§2.1 表示条件をカバレッジ100%に限定 |
| 2 | カバレッジ30%ゲートは選択バイアスを解消しない | codex [Must]2 / tacchi | **30%を廃止し全量（100%）基準**へ。§2.1 |
| 3 | G1 は均衡 corpus なので母集団率を校正しない | codex [Must]3 | §2.8 で根拠表現を訂正 |
| 4 | 穴だけのバックフィルは harness 混在で不可 | codex [Must]4 / tacchi | §2.3 で **(1-A) を却下**し前向き専用に |
| 5 | 過去レコードから provenance は復元できない | codex [Must]5 | §2.5 で過去は `unknown provenance`・将来分は producer 時点付与 |
| 6 | 見かけの改善経路を明記せよ | codex [Must]6 | §2.6 に13経路を列挙 |
| 7 | 集計契約（DISTINCT / `:` 分解禁止 / raw 記録 / orphan surface / TZ） | codex [Must]7 | §2.4 |
| 8 | **backlog drain で過去週が黙って埋まる** | **tacchi [Must]** | §2.2 で **freeze を read 時導出で実現**（`judged_at` が週終了+3日以内の判定のみ採用） |
| 9 | 旧 rework 表示と併存させるな | tacchi [Must] | §2.7 で置換を明記 |
| 10 | 率の数字単体では行動が変わらない | tacchi / codex [Should] | §2.7 で悪化週のみ TP 実発話 TOP3 + 朝レビュー導線 |
| 11 | 部分週の表示ポリシー未定義 | tacchi [Should] | §2.1 で確定週（終了+3日）のみ・進行中は非表示 |
| 12 | PJ mix シフト（Simpson）が最大の交絡 | tacchi / codex [Should] | §2.7 で PJ 別内訳を**必須** evidence 化 |
| 13 | 実施順の改訂は §7.1 のユーザー決定と衝突する | tacchi [Should] | **codex の「実施順改訂」推奨を退け**、§2.9 の表示開始ゲート（k=4週連続）で従属させる |
| 14 | extractor_version も provenance に含めよ | tacchi [Should] | §2.5 |
| 15 | `key` 欠損14行の除外を分母定義に明記 | tacchi | §2.4 |
| 16 | 引用行 `batch.py:302` は provenance 保存行。注入は `prompt.py:46` | tacchi [Nit] | 本文から該当引用を削除（§1.6 に統合） |
| 17 | 「週の切り方は一択」は強すぎる | codex [Nit] | §2.1 で TZ・archive 完全性を別途決定事項として明記 |

### rev3（2026-08-13・codex 差分レビュー1巡）

| # | 指摘 | 出所 | 反映 |
|---|---|---|---|
| 18 | **分子だけ freeze しても分母（`utterances.db` の遅延 ingest）が動けば率は動く** | codex [Must]1 | §2.2 で **3ストア共通 cutoff** に拡張（分母 `ingested_at <= cutoff`・分子 `detected_at <= cutoff`）+ 確定後の更新削除禁止契約 |
| 19 | 重複・再判定時の競合解決が未定義 | codex [Must]2 | §2.2 に競合解決を明記（最古の有効判定を採用 / 相反 verdict は集計失敗 / 削除検知で系列を出さない / `judged_at` 書換え禁止） |
| 20 | 「分子 ⊆ 分母」だけでは守れる範囲が狭い | codex [Must]3 | §2.4 の実行時検証を4条件 + 明示エラーに拡張 |
| 21 | D=3 の根拠不足（週最大 1,566件 > 週上限 1,400件） | codex [Should] | §2.2 で **仮の運用値**と明記し、実測 p90/p95 で再設定。誤差は「未測定週の増加」に倒れる旨も明記 |
| 22 | `judged_at` 欠損分は再判定防止 key として残るため永久に系列外 | codex [Should] | §2.2 に明記。系列開始日**以後**の欠損は producer/schema 異常としてその週を未測定に |

**判定**: rev3 で codex [Must] 3件・[Should] 2件をすべて反映。tacchi は rev2 の4点反映で
「実装着手可」の条件を明示済み（条件充足後に巡を重ねない）。**設計は確定・実装着手可**。
