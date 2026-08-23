# #541 設計メモ v3（tacchi レビュー2巡反映後・実装着手可）

v1 `設計修正要`（[Must]4件）→ v2 `条件付き着手可`（[Must]3件）→ **v3 で3点とも反映済み。再レビュー巡は不要（レビュアー明示）**。

## Must1 決着 — 反映経路を実測で確定（済・2026-08-24）

**結論: 今回の反映は reflect フローの外で起きた**（tacchi の見立てが正しい。v1 の「フロー内で promote を忘れた」は誤り）。

証拠（いずれも実行済みコマンドの結果）:
- `grep -rl "measure-now-not-later" ~/.claude/evolve-anything/` → **0 件**。`optimize_history/`（`evolve-anything.jsonl` ほか）にも参照なし。reflect の apply / rule_apply 記録が一切存在しない。
- 08-23 の transcript で `measure-now-not-later` を含むのは、当該 rule を起草した通常セッションの subagent ログ 3 本（`agent-atacchi-measure-now-*` 等）のみ。＝ユーザー発話への即応で Claude が直接 Write した新規ルール。
- 新規ファイルは rule_apply 履歴に載らない（`reflect.py:546-552` `new_file_not_revertible`）ため、履歴ゼロは仕様どおり。

→ **操作履歴を使う検出は今回のケースを原理的に拾えない**ことが確定。

## v1 からの変更

### 落とすもの

- **B（「反映済みだが未 promote」を操作履歴で検出）を削除**。[Must] 2 に同意。promote が無ければ correction も apply 記録も生まれず、promote があれば `reflect.py:1071` が seen を同時に書くため再提示されない。検出対象の状態集合が空。今回の実インシデントも Must1 の実測どおり履歴ゼロで、B では拾えない。
  - これに伴い [Must] 4（B の母集団計測）は**対象消滅により解消**。B を残さないので母数を測る意味がない。
- **A（reflect フロー内で promote を必須化）を縮小**。指示文は既に promote-first（`proposal_digest.py:625-640`）であり、今回はそのフロー自体を通っていないため、A を強化しても今回の事故は防げない。v1 の「コード側で状態を作らせない」は enforcement 点が存在しないので撤回する。

### 本命に据えるもの

- **D（新規・v1 [Must]3 の提案を採用）: 選択肢に「既に反映済み → 既読にする」を追加する。**
  - 現状の4択は ①共通ルールに書く ②このPJのルールに書く ③いまは反映しない（記録のみ）④いいえ。**既に反映済みのときに合う選択肢が無い**（③は「未反映」を含意する）。今朝ユーザーが「もうそのルール無い？」と聞いたのは、この欠落がそのまま出た形。
  - 前提として認めること: **再提示そのものは完全には防げない**（フロー外反映を検知する手段が無い）。防げないものを防ぐ設計にせず、**出たときの逃げ道を1タップにする**のが本設計の立場。

  **D-1 選択肢の収め方（v2 [Must]3 決着・実測 + ユーザー裁定）**
  - 実測: `AskUserQuestion` の `options` は **`maxItems: 4`**（ツールスキーマ）。「Other」は自動付与で、5つ目は追加できない。よって「4択に足す」は文字どおりには実装不能。
  - ユーザー裁定（2026-08-24）: **①②を「ルールに書く」1つへ統合**し、空いた枠に「既に反映済み（既読にする）」を入れる。反映先（共通 / このPJ）は**選択後に AI が提案し、ユーザーが一言で直せる**方式にする。既存4つの意味をどれも捨てずに済むため。
  - 新しい4択: ①ルールに書く ②いまは反映しない（記録のみ） ③既に反映済み（既読にする） ④いいえ（記録も反映もしない）

  **D-2 実体（v2 [Must]1 決着・`--promote-weak` は使わない）**
  - `--promote-weak` は seen 記録に加えて **correction を `reflect_status="promoted"` で新規作成**する（`promote.py:346-392`）。#514 の在庫レーンは `reflect_status == "promoted"` ∧ 非 invalidated を「反映先未定の積み残し」として出し続ける（`correction_backlog.py:106-110`）。**そのまま使うと weak レーンから消える代わりに在庫レーンで蒸し返される＝再提示バグの引っ越し**になる。
  - 正しい実体は **`record_reviewed(decision="already_reflected")` のみ（correction 非生成・promote 非実行）**。seen は C 反映後の全レーン（daily / digest / bootstrap / `filter_actionable`）で除外キーとして効く。`decision` は自由文字列で read 側は key 集合しか見ない（`daily_review._read_seen_one`）ため新値追加は安全。**新 store 不要**＝#379 の新設凍結をクリア。
  - **計測（延期契約の代わり・codex M6 是正で正確化）**: 集計器は無い（#379 凍結中につき新設しない）。ただし `decision="already_reflected"` が既読ストアに残り続けるため、必要になったときに**後から一意 signal_key 数として数えられる**（行数ではなく key の一意集合。`record_reviewed` はロック外で既存集合を read するため、並行実行では同一 key が複数行 append されうる — read 側は set 化で無害だが、行数をそのまま件数として数えると水増しになる）。「今後ゼロコストで前向きに自動計測される」は誤りで撤回する。別途の計測タスクを将来へ延期しない、という結論自体は変わらない（集計コマンド1本で後追い可能）。

  **D-3 変更箇所は3つ（v2 [Must]2。1つでも落とすと部分修正になる）**
  - `scripts/lib/daily/proposal_digest.py`（`_reflect_choice_lines`）— SessionStart digest レーン
  - `skills/evolve/references/correction-review.md` — 反映先つき4択の手順
  - **`skills/evolve/SKILL.md`** — evolve Step 6.2 の本体レーン。**これを落とすと毎朝の確認本体が旧4択のまま残る**

- **C（維持）: bootstrap 経路にも既読ゲートを通す。** `bootstrap_backlog.py:362-377` `_scope_backlog_candidates` は promoted / TTL しか見ておらず seen を見ない。ここに `filter_actionable` 相当（seen の promoted / rejected / deferred を除外）を通す。

### Should 反映

- **S1 data_dir 分裂の assert**: SessionStart 表示は `collectors.py:520` が digest data_dir を明示 path 単読、`record_reviewed` は store_write 正準 + union read。食い違うと promote 済みでも翌朝再提示される（`pitfall_datadir_hook_tool_split` と同型）。読み書きの data_dir 同一性を検査する契約テストを1本置く。
- **S2 choice③ の整合を1行確定**: ③「いまは反映しない（記録のみ）」も `--promote-weak` を実行する（`proposal_digest.py:637`）ため correction が reflect_status 未 applied で作られ、#514 の修正在庫レーンに積まれ続けるように読める。「記録のみ・反映しません」の約束と在庫レーン再提示が矛盾しないよう、deferred の扱いを実装時に確定して1行書く。
- **S3 signal_key の再生成安定性**: compaction / 再 ingest で同一発話が別 key になると seen をすり抜ける。judge の dedup が provenance 決定論であることをテスト1本で確認。

## 追加観測: 第3の再提示経路（2026-08-24・別セッションで実演された実データ）

並行セッション（ses-main）が、まさに本 issue の事象を**その場で作ってしまった**。設計の場合分けに直接使える。

- weak `153f137942e8b0eb` → `~/.claude/rules/runtime-availability-matrix.md`（新規）
- weak `735ffdbc16d6c741` → `<PJ>/.claude/rules/observation-first.md`（新規）
- 処理の実際: **`--promote-weak` は両方通した。ルール本文は手書きし、`--apply` は通していない。**

これは我々の実測2件（履歴ゼロ＝フロー外反映）とは別の**中間状態**:

| 経路 | promote | rule 本文 | --apply | 症状 |
|---|---|---|---|---|
| 1. フロー外反映（今回の #541 実例2件） | 無 | 有 | 無 | **weak レーンで翌朝再提示** |
| 2. 正常 | 有 | 有 | 有 | 出ない |
| 3. **中間（ses-main の実例2件）** | 有 | 有 | 無 | weak レーンからは消えるが、`reflect_status="promoted"` のまま **#514 在庫レーンで「まだ反映されていません」と蒸し返される** |

経路3は tacchi [Must]1 が机上で指摘した「バグの引っ越し」が**実データで確認された**形。D で promote を呼ばない判断の正しさを裏付けると同時に、**既存の①②（ルールに書く）にも同じ穴がある**ことを示す（`--apply` を通し損ねると経路3に落ちる）。

→ 実装スコープに **S4** を追加する（下記）。経路3は「rule 本文が書かれたか」をコードから知る手段が無い（`--apply` こそがその紐付け）ので、**検出器は作らず導線で塞ぐ**。

## 未実測のまま進める前提（明示）

- **「反映済みなのに再提示される」実頻度は未計測**（確認できた実例は 2 件）。測ろうとすると全 weak 474 件 × rules 35 本の意味判定が要り、今日の手段は LLM バッチのみ。D は選択肢の文言追加＋既存コマンド1本で、**頻度が低くても害が無く・高ければそのまま効く**ため、頻度確定を待たずに進める。
  - measure-now-not-later の3問: ①今日 LLM 無しで生成する手段は無い（字面照合は本 issue で棄却済み）②片側の結論は出ている＝実例2件で「選択肢が無くて詰まる」ことは実証されており、D の採否はこれで決まる ③代理測定は不可（corrections の reflect_status は反映済み判定を含まない）。

## 実装スコープ（この PR）

1. **D**: 選択肢を「①ルールに書く ②いまは反映しない ③既に反映済み ④いいえ」に再構成（`proposal_digest.py` + `correction-review.md` + **`skills/evolve/SKILL.md`** の3箇所）。③の実体は `record_reviewed(decision="already_reflected")` のみ（**promote を呼ばない**）。①選択後に反映先（共通 / PJ）を AI が提案する導線を1行入れる
2. **C**: bootstrap に既読ゲート（`bootstrap_backlog.py:362-377`）
3. **S1**: seen の read/write data_dir 同一性の契約テスト
4. **S2**: 「いまは反映しない」と #514 在庫レーン再浮上の整合を1行確定（`correction-review.md:125-151` の「deferred は意図的に再浮上」と、D の誤った再浮上を区別する）
5. **S3**: signal_key 決定論のテスト1本
6. **S4**（追加観測の経路3対策）: 「①ルールに書く」の手順を **promote → 本文起草 → `--apply`** の3点セットとして3箇所の文言すべてに明記し、`--apply` を通し損ねた状態（`reflect_status="promoted"` のまま）が在庫レーンに出たとき、**それが「ルールに書いたが紐付けが済んでいない」ケースであると読み取れる文言**にする。検出器は作らない（rule 本文が書かれたかをコードから知る手段が無いため）。**新 store を作らないこと**

## 検査の有効性（実装者への要求）

追加したゲート・テストは、**通したまま仕様を壊せる書き換え**を実際に適用して赤くなることを確認する。最低4種の独立した変異（①要素を消す ②語を残して意味を壊す ③分散・入替 ④検査自体を無効化する）を各1件以上、実際に適用して結果を報告すること。加えて陽性対照（正常データで誤検出しないこと）を別途置く。委譲側が列挙した回避手段とは**種類の違うもの**を2件以上自分で構成し、緑のまま残ったものが1件でもあれば完了扱いにしない。各変異には「壊す不変条件」と「通したい検査経路」を書き、両方が同じ変異は重複として数えない。

変異の具体例（下限であって上限ではない。ここに挙げたもの**だけ**を試して終わりにしない）:
- ④の例（v2 [Nit]）: **S1 の契約テストを data_dir 片側 mock で骨抜きにする** — read/write 両方を同じ fixture に向けると恒真になり、分裂を検出できないまま緑になる。この変異で赤くなることを確認する。
- ②の例: `decision` 文字列は残したまま値を `"already_reflected"` 以外へ差し替える（read 側が key 集合しか見ないため、値の取り違えは静かに素通りしうる）。
- 探索すべき入力クラス: 空白 / Unicode 正規化差 / 改行混入 / 巨大入力、および実行文脈（順序・並行・data_dir 差し替え・キャッシュ済み古い成果物）。

## 再レビューで見てほしい差分

- Must1 の結論（フロー外反映）と、そこから B/A を落とした判断が妥当か
- D が本当に朝の4択の体験を変えるか。文言だけで終わっていないか
- C・S1〜S3 の中に、これも no-op になるものが混じっていないか
