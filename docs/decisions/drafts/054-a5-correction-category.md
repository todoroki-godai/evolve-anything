# ADR-054 A5 — 指摘カテゴリ（`provenance.category`）の設計

**状態**: rev3（2026-08-13・codex 1巡 + tacchi 1巡反映）
**親**: [054-four-pillars-completion-design.md](../054-four-pillars-completion-design.md) §7.1 Phase A / §6 実施順
**関連**: [drafts/054-c-a-numerator.md](054-c-a-numerator.md)（C(a) 指摘率。本設計はその内訳軸を作る）

> **rev2 でスコープを縮小した。** rev1 は「`correction_type` を細分化して `correction_recurrence`
> を測定可能にする」設計だったが、codex [Must] を受けて**その部分を全て削除**した。
> A5 は **judge 判定時に `category` を1つ付け、C(a) の TP をカテゴリで分解して見せる**だけになる。
> 縮小の根拠は §2.0。

---

## 1. 実測（2026-08-13・実データ）

### 1.1 `correction_type` の現状分布

`~/.claude/evolve-anything/corrections.jsonl` 全160件:

| correction_type | source | 件数 |
|---|---|---|
| `semantic_idiom` | `reflect_confirmed` | **150** |
| `stop` | `backfill` | 8（機械生成・`provenance_weight` が除外） |
| `iya` | `hook` | 1 |
| `naoshite-request` | `hook` | 1 |

書き手は `correction_semantic/promote.py:278` の定数1箇所だけ。

### 1.2 `correction_recurrence` が測定不能な理由

`audit/outcome_metrics.correction_recurrence_rate`:
- 分母 = 窓（30日）内の distinct `correction_type` 数 / 分子 = 2セッション以上で発生した type 数
- `MIN_DISTINCT_TYPES_FLOOR = 5` 未満は `insufficient_sample` で None

実質1種なので floor に永久に到達しない。ADR §2.4 の「測定不能」はこれ。

### 1.3 有効 correction の実内容（invalidated 8件を除く）

**まず母数を分解する**【tacchi [Should]5】。`semantic_idiom` の有効142件を `weak_signal_channel` で割ると:

| channel | 件数 | 本文の性質 |
|---|---|---|
| `llm_judge` | **121** | 人間の実発話（`provenance.text` あり） |
| `rephrase` | 13 | 決定論チャネル。`provenance.text` を持たない |
| `verbosity` | 8 | **機械判定文**（「冗長判定（over_summary）: …」）。人間発話ではない |

下表の塊は **`llm_judge` の121件**から帰納した（`rephrase` / `verbosity` の21件は本文が空か機械文で、
どの塊にも寄与していない）。**「142件から帰納した」と書くのは出所として不正確**なので訂正する。

`llm_judge` 121件の `weak_signal_provenance.text` / `.reason` を目視した結果、自然に立ち上がる塊:

| 塊 | 概数 | 実例 |
|---|---|---|
| 見た目・レイアウト・表示崩れ | ≈30 | 「P6のデザインが違うんだけど」「備考が切れてる」「ロゴの場所もうちょっと右じゃない？」 |
| 説明が長い・わかりにくい | ≈12 | 「もうちょっと簡潔に」「わかりずらい、、、」 |
| やり方・方針への異議 | ≈12 | 「共通のhtml用意してincludeすればいいのに」「globalのhookを1つ1つ見ればよいんじゃないの？」 |
| 事実・前提・認識の誤り | ≈10 | 「いやいや、matsukaze-mindenはrepositoryをいろいろみれるって」「prodはprodシート、dev/stgは練習用だよ？」 |
| やり残し・不足 | ≈8 | 「Alchemy/Infuraの具体名もいれておいて」「詰めて」 |
| 余計・削除要求 | ≈5 | 「橘さんレビュー用って言葉けして」「P1のupdater inc 左側はいらなくない？」 |
| 手順・ツール・ルールの不遵守 | ≈6 | 「lspを使わなかった理由はなに？」「実際にブラウザで見た目確認したの？」 |

**この分布は実データからの帰納であり、先に語彙を決めてデータを当てはめたものではない。**

### 1.4 判定プロンプトが今持っている軸は「形」であって「対象」ではない

`prompt.py` は修正の**文型**（後置型 / ソフト指摘型 / 観察型 / 明示否定型）を例示しており、
`reason` にもその語が echo される。しかし「何を直させられたか（対象）」を測るのに文型は使えない。
`後置型` が2セッションで再発しても学習可能な事実を1つも含まない。

---

## 2. 確定案（rev2）

### 2.0 `correction_type` は変更しない / `correction_recurrence` は復活させない【codex [Must]・rev2 の中核】

rev1 は `correction_type` を `semantic:<category>` に変え、`correction_recurrence` を
floor 越えさせる設計だった。**取り下げる。** 理由:

1. **飽和**: 8個の粗いカテゴリでは、窓内に十分な件数があればほぼ全カテゴリが2セッション以上に出現し、
   再発率は 1.0 に張り付く。動かない数字を出すのは #376（数字が嘘をつかない）の別形
2. **混合集合**: prefix を付けても hook レーンの `iya` / `naoshite-request` は同じ分母に残る。
   分母は「対象カテゴリ + hook パターン名 + legacy `semantic_idiom`」の混合のままで、
   何を数えているか説明できない。prefix は混在を可視化するだけで指標を修復しない
3. **legacy の混入**: 既存142件は `timestamp` = **promotion 時刻**（`promote.py:275`）なので、
   30日窓から抜けるまで巨大な1 type として分母・分子を歪める。
   これは本 ADR が2度踏んだ「異なる分類規約を系列を分けずに混ぜる」罠の3度目にあたる
4. **floor なしの下流**: `outcome_attribution._correction_recurrence`（:153-157）と
   `outcome_promotion_readiness`（:134）は `correction_type` をそのまま grouping key にし、
   **floor を持たない**（実コード確認済み）。`correction_type` の意味変更は per-skill / PJ 側の
   recurrence 系すべてを同時に、無防備に意味変更する
5. A5 は親 ADR で「vanity gate」として成果指標から降格済み。越えること自体に価値はない

**帰結**: `correction_recurrence` は **設計判断として恒久的に `insufficient_sample`（測定不能）**とする。
これは嘘ではない（「測っていない」と表示している）。指標そのもののコード削除は本設計のスコープ外
（`outcome_promotion_readiness` の3軸ロジックに波及するため）。別 issue とする。
**親 ADR §2.4 の3軸表と §7.1 の A5 行をこの裁定で更新する。**

**飽和ゲートを決定論で入れる**【tacchi [Must]1】: 上の裁定は「今は floor に届かない」という
現状依存であり、hook レーンの `CORRECTION_PATTERNS` が増えれば floor 5 に達し得る。
達した瞬間に「再発率 1.0」が audit へ**自動表示される経路が既定で開いたまま**では、
「floor 到達後に人間が実測して判断する」という記憶頼みの TODO になり #376 の同型になる。
→ 本 PR で `correction_recurrence_rate` に決定論ゲートを入れる:

> **全 type が recurring（= `recurring == distinct_types`）かつ `rate >= 0.9` なら raw 値を返さず、
> `reason="saturated"` として値なしで surface する。**

「語彙が粗いだけなのに悪化に見える」数字を、人間が気づく前に構造で止める。
（`insufficient_sample` と同じ「値を出さない + 理由を出す」既存パターンに揃える。新 section を作らない）

### 2.1 何を作るか

判定時に **`category`（対象軸の8値 enum）を1つ返させ**、weak_signal の `provenance` に保存する。
**`correction_type` には触らない。**

| label | 意味 | §1.3 の塊 |
|---|---|---|
| `presentation` | 見た目・レイアウト・表示崩れ・図表の読みにくさ | 見た目 |
| `explanation` | 説明が長い・難しい・わかりにくい | 説明 |
| `factual` | 事実・前提・認識の誤り（値の取り違え含む） | 事実 |
| `approach` | やり方・方針・設計そのものへの異議 | やり方 |
| `omission` | やり残し・不足・詰めが甘い | やり残し |
| `excess` | 余計・不要・削除要求・やりすぎ | 余計 |
| `process` | 手順・ツール・ルールの不遵守（使うべきものを使わなかった） | 手順 |
| `other` | 上記のどれでもない | — |

**境界の優先規則**（codex [Should] / tacchi [Should]4。表だけでは判定が揺れる）:
- `presentation` vs `explanation`【最頻の揺れ。実例「わかりずらい、、、完結な方がよくない？」は
  スライド文言＝成果物なので `presentation`】: **成果物の見た目・文言**なら `presentation`、
  **Claude 自身の説明・回答**なら `explanation`
- `approach` vs `process`: 設計選択そのものへの異議は `approach`。**既に合意済み・明文化済みの手順**への違反だけ `process`
- `omission` vs `excess`: 欠けている成果物・要件は `omission`。存在する不要物の削除要求は `excess`
- `factual` vs `approach`: 検証可能な前提・値の誤りは `factual`。前提が正しくても選択が不適切なら `approach`
- 複合発話は**主たる修正対象を1つ**選ぶ。同率のときは `other` に逃がさず、上記の並び順（factual > process > omission > excess > presentation > explanation > approach）で固定する

`other` 比率は**カテゴリ内訳行の一項目として surface**する（新指標・新 section にはしない）。
語彙が実態に合っていないことは `other` の比率で事後検証する。

### 2.2 決定論分類でなく LLM 分類にする理由

| 案 | 判定 |
|---|---|
| (A) 後段の決定論キーワード分類 | **不採用**。§1.3 の実文（「なんか、スクロールするのが違和感なんだよなぁ」「詰めて」）は語彙で分離できない。人手ラベルなしに精度を主張できず、主張するなら G1 と同じラベリング費用が再発する |
| (B) 判定時に LLM に1フィールド返させる | **採用**。judge は既に文脈（`prev_action` + 発話）を読んで二値判定している。カテゴリはその副産物として**追加 LLM 呼び出しゼロ**で得られる |

**費用と token guard**【codex [Should] 反映】:
- 増分見積もり: 入力 ≈+250 token/batch（語彙表）× 7 batch + 出力 ≈+10 token/verdict × 200 件
  ＝**1日あたり +約3.7k token**。日次上限は200件 / 150,000 token（`judge_runner.py:66`）
- **ただし現行の `estimate_tokens` はこの増分を見ない**。固定費 400 token/batch を定数で持ち（`batch.py:399`）、
  LLM の**出力** token も見積もりに入っていない。
  → 本 PR で **固定費を実際の `build_batch_prompt([])` の長さから導出**し、**出力予算を加算**する。
  「実測値を PR に書く」だけでは上限の過小評価は直らない

### 2.3 前向き専用 — 既存レコードを遡及分類しない

既存 351 weak_signal / 142 correction は `category` を持たない。**遡及付与しない**。
理由は C(a) §2.3 と同一: 発話時と別条件（別プロンプト）で測った値を同じ系列に混ぜるのは、
この ADR が2度踏んだ罠の3度目になる。

### 2.4 category は「事実」でなく「その judge 実行時の測定値」【codex [Must]】

同一物理発話が再判定される経路が実在する:
- 応答欠損・JSON 不正は judged にせず再試行（`batch.py:247`）
- 部分応答の欠落 index も未判定のまま再試行（`batch.py:277`）
- weak_signal → idiom → judged は順次書込（`batch.py:330`）。weak_signal 書込後・judged 書込前の中断で
  同一物理発話が再判定され、**別 category の重複 signal**が生じ得る

したがって:
- `category` と一緒に **`model` / prompt fingerprint / category schema version** を **producer 時点で保存**する
  （集計時に現在値を付けない）
- 集計時に**同一 physical key の複数 category を検出**したら、黙って多数決・最新値を採らず、
  **その週を未測定または明示エラー**にする（C(a) §2.4 の競合解決と同じ規律）
- `is_correction=false` のとき `category` は**必ず `None`**。`true` のときのみ enum を要求する契約にする
- enum 不正値は verdict 全体を落とさず `category=None` に正規化する

### 2.5 C(a) 系列との関係 —— マージ期限がある

プロンプト変更は C(a) §2.5/§2.6 の系列断絶条件（prompt fingerprint の変化）に該当する。

ISO 週の実測: **W33 = 2026-08-10（月）〜08-16（日） / W34 = 08-17（月）〜08-23（日）**。
~~daily runner の安定稼働は 08-11 以降なので W33 は 100% に届かず、**C(a) の系列起点は W34**。~~

**【2026-08-18 実測で訂正・#508 M2/M10】上記は W33 進行中に書かれた予測であり、実測ではなかった。
#508 の dry-run 実測では W33（08-10〜08-16）は 629/629 = 100%・`measured=True` に到達している
（ただし `best_run_length=0` — `measured=True` の週は現時点で W33 の1件のみなので、系列表示ゲート
k=4 週連続はこの実測時点でも未成立。系列起点が実際に W33/W34 のどちらになるかは、その後何週が
連続して 100% に到達するか次第で決まる）。

> **本 PR は 2026-08-17（W34 開始）より前にマージする。**
> 間に合わない場合は起点を W35 にずらし、**その事実を ADR に記録**する（黙ってずらさない）。
> 期限を優先して途中週を同一系列に混ぜてはいけない。

### 2.6 消費先 — C(a) のカテゴリ内訳（新セクションを作らない・#379 非抵触）

`results_board` の指摘率行の直下に、その週の TP をカテゴリ別に分解した1行を出す。

**母集団は C(a) と完全に同一**【codex [Must]】:
- 同じ physical key / 同じ週の切り方（`utterances.timestamp`・UTC・ISO 週）/ 同じ cutoff /
  同じ prompt fingerprint 区間
- **raw `llm_judge` weak_signal の `provenance.category` を join して作る**
- **`corrections.jsonl` の promotion timestamp で数えてはならない**（それは「人間が承認した時刻と選択」を
  測ることになり、C(a) の TP 内訳ではない）

**表示の形**【tacchi [Should]3。当初の「先週は見た目が減ったが手順が増えた」は過剰約束だった】:

C(a) の週次 TP は ≈10〜20件。これを8カテゴリに割ると各セルは 0〜5件で、**週次 delta は雑音**。
さらに全 PJ 合算では **task-mix 交絡が支配的**（実測: `project_path` は amamo 40 / rl-anything 28 /
receipt 17 …。「見た目の指摘が減った」は「今週スライドを作らなかった」とほぼ同義になり得る）。

→ **週次 delta の比較を表示しない**。出すのは:
1. **今週の構成比**（カテゴリ別の割合。`other` 比率を含む）
2. **最大カテゴリの実発話1件**（C(a) §2.7 の「悪化週 TOP3」と同じ流儀。数字でなく自分の言葉を思い出させる）
3. **task-mix 交絡の注記**（カテゴリ構成は「その週に何をやったか」に強く依存する旨の1行）

**カバレッジ**【tacchi [Must]2 の rev2 版】: C(a) の分子は `channel=llm_judge` のみなので、
カテゴリ内訳は**分子の 100% をカバーする**（構造的な非分類レーンは分子側に存在しない）。
ただし **corrections への昇格全体で見ると 15%（21/142）は llm_judge 非経由**（`rephrase` 13 /
`verbosity` 8）であり、これらは C(a) の分子にも内訳にも入らない。**これは C(a) 自体のスコープ限界**であって
A5 が作る穴ではないが、内訳を「指摘の全体像」と読ませないよう表示に but 明記する。

`correction_recurrence` は §2.0 のとおり**復活させない**。内訳が本設計の唯一の消費先。

**実データで見つけた注意点**（tacchi 実測）: 同一発話が3重に昇格しているケースが1件ある
（142件中3行）。C(a) の分子・分母は `COUNT(DISTINCT physical_key)` なので防御済みだが、
内訳も**同じ physical key 単位で数える**こと（correction 行単位で数えると3重計上になる）。

---

## 3. 実装スコープ

| 対象 | 変更 |
|---|---|
| `correction_semantic/prompt.py` | プロンプトに語彙表 + 優先規則 + `category` フィールドを追加。`_validate_verdict` で enum 厳格検証（不正は `category=None`）。`is_correction=false` は `category=None` 強制。module docstring の verdict schema 記述も更新 |
| `correction_semantic/batch.py` | verdict → weak_signal `provenance` に `category` + `model` + prompt fingerprint + category schema version を透過。`estimate_tokens` の固定費を `build_batch_prompt([])` から導出し出力予算を加算 |
| `correction_rate.py`（C(a) の module） | カテゴリ内訳の集計を追加（同一 key に複数 category → 当該週を未測定/明示エラー） |
| `results_board.py` | 指摘率行の直下に内訳1行（`other` 比率を含む）。**内訳は「あれば出す」optional**（category を持たない週で壊れない） |
| `promote.py` | **変更しない**（`correction_type` は `semantic_idiom` のまま） |
| `audit/outcome_metrics.py` | `correction_recurrence_rate` に**飽和ゲート**を追加（§2.0。`recurring == distinct_types` かつ `rate >= 0.9` なら値を返さず `reason="saturated"`） |
| 親 ADR | §2.4 の3軸表と §7.1 の A5 行を §2.0 の裁定で更新 |

**新設ゼロ**: 新 store / 新 observability section / 新 weak_signal channel / 新 advisory adapter のいずれも作らない。
`weak_signals.jsonl` の `provenance` へのフィールド追加は既存 store のスキーマ拡張であり、
`shrink_freeze.py` の機械的な新設4種に該当しない（codex 確認済み）。

## 4. rev1 → rev2 のレビュー反映表

| 指摘 | 反映先 |
|---|---|
| codex [Must] §4-4: `correction_recurrence` の復活を止めよ | §2.0（全面削除・恒久 not_measured の裁定） |
| codex [Must] §4-3: `semantic:` prefix は例外は出さないが集計の意味を壊す（floor なしの下流3件） | §2.0-4（`correction_type` 不変に変更） |
| codex [Must] §2.3: legacy 142件は promotion 時刻基準で30日間分母を歪める | §2.0-3 |
| codex [Must] §4-2: category は事実でなく測定値。再判定経路で重複 category が生じる | §2.4（provenance 保存・重複検出・`is_correction=false` 契約） |
| codex [Must] §2.6: 内訳の母集団を C(a) と同一にせよ。promotion timestamp で数えるな | §2.6 |
| codex [Should] §4-1: 8カテゴリは妥当だが境界の優先規則が要る | §2.1（優先規則4組 + 同率時の固定順） |
| codex [Should] §2.2: token guard に増分が反映されない | §2.2（`estimate_tokens` の固定費導出 + 出力予算加算を実装に含める） |
| codex [Should] §4-5: 08-17 は W34 の開始日。C(a) 文書の「W34（08-17 終了）」も誤り | §2.5（ISO 週を実測して訂正）+ C(a) 文書 §4 を訂正 |
| codex [Nit]: `prompt.py` docstring の verdict schema | §3 |

## 5. rev2 → rev3 のレビュー反映表（tacchi 1巡・2026-08-13）

| 指摘 | 反映先 |
|---|---|
| [Must]1: 飽和時に raw 値が audit へ自動表示される経路が既定で開いたまま。「初回実測で判断」は柱4違反 | §2.0 末尾（決定論の飽和ゲートを本 PR に含める）+ §3 |
| [Must]2: category 欠落は例外でなく構造（昇格の15%が llm_judge 非経由） | §2.6「カバレッジ」（rev2 で `correction_type` を触らなくなったため distinct_types の三重混合は解消。残る事実は C(a) のスコープ限界として明記） |
| [Should]3: 週次 delta は雑音・task-mix 交絡が支配的。構成比 + 実発話1件へ | §2.6「表示の形」 |
| [Should]4: presentation / explanation の境界判別1行 | §2.1 優先規則の先頭 |
| [Should]5: §1.3 の母数に verbosity 機械判定文が混ざっている | §1.3（母数を llm_judge 121件に訂正 + channel 内訳表を追加） |
| 実測: 同一発話の3重昇格が1件 | §2.6 末尾（内訳も physical key 単位で数える） |
| 実測: `semantic:*` は全 reader で既定枝に落ちる（無影響）を独立確認 | rev2 で prefix 自体を廃止したため反映不要（codex 結論と一致） |

**tacchi の「より良い案」（`verbosity` → `semantic:explanation` の決定論写像）は採らない。**
rev2 で `correction_type` を触らない方針に変えたため、写像先の `semantic:*` 名前空間自体が存在しない。
`verbosity` レーンは C(a) の分子の外側にあり、内訳の穴にはならない（§2.6 カバレッジ）。
