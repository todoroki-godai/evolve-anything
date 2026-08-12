# ADR-054 Phase A / A0 — correction capture の修理（実装前設計・codex round2対応版）

- **Status**: **設計確定**（頭の裁定8点を反映済み。codex 巡は本版で打ち切り、未決事項ゼロ。実装フェーズへ移行可能）
- **対象**: ADR-054 §5 Phase A の A0（`hooks/correction_detect.py` が呼ぶ検出ロジック本体は
  `scripts/lib/rl_common/detection.py` の `CORRECTION_PATTERNS` / `should_include_message` /
  `detect_correction`）
- **実測日**: 2026-08-12（round1 headline は再現不能と判明→round2 で harness 修正・全面再測定→
  round2 codex 指摘 [Must-6]系を反映し本版で再々測定）
- **本文書のスコープ**: A0 のみ

> **訂正履歴**: round1「新規9件・TP8件・精度89%」は保存されない対話内 exploration の値で
> 再現不能だった（round2 で訂正）。round2「census 7/9=77.8%・sample recall 0/11」は
> ラベル台帳に文脈を保存していなかった点・sample 生成方法が文書と不一致だった点を codex
> round2 で指摘され、本版（round3）で **文脈を persist した上で再ラベル**・**harness のサンプリング
> 方法を文書どおりに統一**して再測定した。**結論の数値は変わらないか、僅かに変動した
> （census 7/9→7/9 のまま・sample TP率 13.75%→12.5%・recall 0/11→1/10）**。下がった値も含め
> そのまま報告する（頭の裁定「結論より数値を優先」に従う）。

---

## 0. 結論（先出し・round2訂正版）

- ベースライン（07-27〜08-12 固定窓・2,841件・too_long 1,503・skip 214・include 1,124・
  ヒット0件）は今回も完全再現
- **census（全数・文脈persist済み・複合動詞除外パターン）: 新規ヒット9件、真陽性7件
  （precision 77.8%、Wilson95% CI [45.3%, 93.7%]）**。9件全件の直前 assistant 発言を
  raw transcript から実際に取得し `a0_full_census.json` に保存した上で再ラベル。
  round1で「文脈欠落のまま命令形だけでTP認定」していた4件のうち3件
  （本PRで直しておいて／見つけた2件も直して／P1・P2とも実バグだね）は
  **具体的な直前AI発言（issue起票報告・codexレビュー指摘・欠陥2件の自己申告）と正確に対応する
  ことを確認**でき、TP判定の妥当性が文脈で裏付けられた。残り1件（やめてほしい）は
  `/clear` 直後で本セッション内に prior が存在しないが、外部PRコメントURLという
  独立した証拠でTPと判断。この結果 **census の TP 数は 7/9 のまま変わらなかった**
  （文脈確認により1件の判定が入れ替わりうる不確実性はあったが、実際には変わらなかった）
- **`_MACHINERY_MARKERS` 追加後: 7/8 = 87.5%（Wilson95% CI [52.9%, 97.8%]）**
- **独立ラベル評価集合（harness 単体で再生成可能な「n=80単純無作為＋machinery全数」抽出、
  seed=20260812 再適用）**: 母集団の真の修正発話率 **10/80 = 12.5%**
  （Wilson95% CI [6.9%, 21.5%]）。母集団 1,124件への外挿 ≈ **140.5件**
- **Recall（サンプル直接測定）= 1/10 = 10.0%（Wilson95% CI [1.8%, 40.4%]）**。
  今回のサンプルには候補ヒットが偶然1件含まれ（`訂正しておいて`）、これは ground-truth でも
  TP と判定済みの発話であり、census の判定と整合した。**FN（見逃し）= 10−1 = 9件**
  （round1 は recall 0/11 なのに「9件見逃し」と誤記していた矛盾を codex に指摘された。
  今回は 10 件中 1 件捕捉・9 件見逃しで算術的に正しい）
- **素朴な合成 recall 点推定 = census真陽性7件 ÷ 外挿母集団140.5件 ≈ 5.0%**
- **結論は不変**: 「capture を直した」ではなく「hookからのcorrections記録が実質死んでいた
  状態から、低コスト・高precision・低recallの小さな追加検出を導入する」。真の capture
  改善（recall の底上げ）は `correction_semantic`（llm_judge 意味判定）チャネルの役割
- **新規（頭の裁定 #3）**: `capture_rate.py` の「hook N件」表示が実際は source を見ずに
  全チャネル（reflect_confirmed 含む）を数えていたバグ（ADR §2.6-5 既知の「嘘をつく数字」）を
  A0 と同一PRで修正する設計を追加（§8）

---

## 1. 頭の裁定（1〜9）対応状況

| # | 裁定内容 | 対応 | 参照節 |
|---|---|---|---|
| 1 | [Must-6最重要] 文脈をラベル台帳に保存・4件を再ラベル | `prior_assistant_text` を `a0_eval_set.jsonl`・`a0_full_census.json` 双方に persist。lookback窓を30→150行に拡張し、round1で空だった4件全てに実文脈を取得。再ラベルの結果は §0・§4.1 に記載（結果的に census は 7/9 のまま） | §2.2, §4.1 |
| 2 | [Must-3部分解消] harnessのサンプリングを文書どおりに統一 | `sample_random_plus_machinery_oversample()` を新設し、`dump-sample` が単一関数で「n=80単純無作為＋machinery全数（重複除く）」を生成。ドキュメントと実装の乖離を解消 | §2.3 |
| 3 | [Must-9部分解消+Must-8未解消] capture_rateのsource×pattern_version分離を実装差分に含める | `compute_capture_rate()` のループに `source=="hook"` フィルタを追加（ADR §2.6-5 の「source を見ず channel を hook 決め打ち」バグの根治と同一箇所）+ `hook_pattern_version_counts` を新設。具体的コード差分を §8 に記載 | §8 |
| 4 | [Must-new] 監視閾値の単位矛盾を解消 | precision で統一（FP率表記を削除） | §7.3 |
| 5 | [Should-new] FN記述の矛盾を修正 | recall=1/10 → FN=9件、と算術的に正しい形で記載（round1の「recall0/11なのに9件」矛盾を解消） | §0, §4.3 |
| 6 | [Should-3未解消] 複合動詞を確定（除外） | `(?<!見)(?<!作り)(?<!書き)(?<!考え)(?<!やり)直して` に拡張。harness に実装済み。固定評価集合（regressionテスト）に負例4件を追加 | §9.2 |
| 7 | [Must-4軽量版] population fingerprint | DB本体スナップショットは取らない。`population-fingerprint` サブコマンドを新設し `(source_path,line_no,timestamp,pj_slug,text_sha256)` の JSONL（1,124行）を repo に固定。制約を明記 | §2.4 |
| 8 | [Must-5軽量版] live等価性は証明せず乖離を定量化 | automated prompt 混入率を2回の独立抽出で実測（1.25%〜6.25%）し、precision/recall への影響方向を記述 | §3.3 |
| 9 | ADR本体更新 | 頭が更新（本文書は差分前提のみ記載） | §12 |

---

## 2. harness（repo 内固定版・round2更新）

### 2.1 実体とスナップショット識別

`/Users/matsukaze-takashi/matsukaze-utils/evolve-anything/scripts/bench/a0_capture_replay.py`
（**commit されるのはこの1ファイルのみ**。§2.4 参照）。
固定コーパス窓: `SINCE="2026-07-27T00:00:00Z"`, `UNTIL="2026-08-12T00:00:00Z"`（不変）。

`DB_PATH` はマシン固有の個人パス直書きを解消し、環境変数で解決する（頭が実施済み）:
`A0_UTTERANCES_DB`（明示指定）→ `CLAUDE_PLUGIN_DATA`（プラグインデータ基点）→
`~/.claude/evolve-anything`（既定）の順。ADR-042 resolver の慣例（`resolve_data_dir`）に揃えた形。

DB スナップショット識別（不変・2026-08-12時点）:
`sha256=f6778438fe5e455f8535f260bef9647df6b30968e957a1c4dfa73d8d38af9458`,
`size_bytes=93597696`, `total_rows=18252`, `window_dialogue_rows=2841`。

should_include_message 内訳（不変・再現済み）: `{'too_long': 1503, 'skip_pattern': 214, 'include': 1124}`。

### 2.2 文脈の永続化（codex round2 [Must-6] 対応）

`fetch_prior_assistant_text()` の遡及窓を **30行→150行** に拡張した。実測で判明した理由:
IDE メタデータ行（`attachment` / `last-prompt` / `ai-title` / `mode` / `permission-mode` /
`pr-link` / `file-history-snapshot` — いずれも `type` が `user`/`assistant` でない）が
直前の実 assistant テキストとの間に**最大44行**挟まるケースが実測された
（例: line 593 の "見つけた2件も直して" の直前 assistant テキストは line 549、**44行差**）。

取得した `prior_assistant_text` は **`dump-sample` の出力・`census` の出力の両方に persist** する
（従来は census 側に文脈を保存していなかった）。ラベル付けはこの persist された文脈を読んで
行い、rationale に引用する。

### 2.3 サンプリング方法の統一（codex round2 [Must-3] 対応）

`sample_random_plus_machinery_oversample(pop, n, seed)`:
1. 母集団全体（1,124件、machinery/genuine を区別しない）から `random.Random(seed).sample()` で
   単純無作為に n 件抽出（今回 n=80）
2. machinery-suspect（6件）のうち①の抽出に含まれなかった分を全数追加

この1関数を `dump-sample` サブコマンドが呼ぶ。round1 は「文書は80+6と書きながら実装は
比例配分で machinery 層0件」という乖離があったが、round2 では harness 単体で
`random_sample=80  machinery_oversample_extra=6` が再現される（実行ログで確認済み）。

**注意**: サンプリング方法変更に伴い、同じ seed=20260812 でも round1 とは異なる80件が
抽出される（母集団からの直接抽選か、層別後の抽選かでアルゴリズムが変わるため）。
これは仕様変更であり bug ではない——文書記載の抽出方法を優先する（頭の裁定どおり）。

### 2.4 生発話成果物は commit しない・sha256台帳（頭の追加指示）

`a0_eval_set.jsonl` / `a0_full_census.json` / `a0_sample_dump.json` /
`a0_population_fingerprint.jsonl` の4ファイルは **他PJ（updater-index・amamo 等）の生の
人間発話**を含む（実際に admin stg の Basic認証情報に言及する行や業務内容の断片を含む）。
本 repo は public 化予定のため、この4ファイルは **`.gitignore` に追加済み（頭が実施）で
commit しない**。commit されるのは harness 本体 `scripts/bench/a0_capture_replay.py` のみ。

再現性は sha256 台帳で担保する（頭の裁定7「DB本体スナップショットは取らずハッシュ台帳で
固定」の実体）:

| 成果物 | sha256（先頭16桁） | bytes |
|---|---|---|
| `a0_eval_set.jsonl` | `d30247d23f677edc` | 95935 |
| `a0_full_census.json` | `8579928c67f8e2cc` | 11551 |
| `a0_sample_dump.json` | `81daef23ff09c2fc` | 92356 |
| `a0_population_fingerprint.jsonl` | `d9824bc2f3952ffa` | 360610 |

**再現手順**: これら4ファイルは git 管理外（ローカル `scripts/bench/` に実体が存在する。
削除されていない限り再生成不要）。他マシン・将来の別セッションで再現したい場合は、
同じ固定窓（`[2026-07-27T00:00:00Z, 2026-08-12T00:00:00Z)`）+ 同じ DB スナップショット
（sha256 `f6778438fe5e455f8535f260bef9647df6b30968e957a1c4dfa73d8d38af9458` / 18,252行）で
`a0_capture_replay.py` の `dump-sample`→`census`→`population-fingerprint` を再実行し、
生成物の sha256 が上表と一致することを確認する。

**再現できない要素（明記）**: `a0_eval_set.jsonl` の `label`/`category`/`rationale` は
**人手判断であり、harness の再実行では復元できない**。DB スナップショットが同一でも
`dump-sample` は文脈（`prior_assistant_text`）とテキストのみを再生成し、ラベル欄は空のまま
出力される。このファイル自体を紛失した場合、§4.1 の census 判定表と本文書の記述を
唯一のラベル記録として手動で復元する必要がある（`a0_eval_set.jsonl` は実質的に
一次データと同格の成果物であり、削除しないよう注意）。

### 2.5 population fingerprint（codex round2 [Must-4] 軽量版）

```bash
python3 scripts/bench/a0_capture_replay.py population-fingerprint
```

DB 本体（93MB・他PJ実発話含む）のスナップショットは取らない（頭の裁定）。代わりに固定窓
母集団（1,124件）の `(source_path, line_no, timestamp, pj_slug, text_sha256)` を
`scripts/bench/a0_population_fingerprint.jsonl` に固定した（本文非保存）。

**制約（明記・silence≠evaluated）**: DB の再抽出・backfill 後にこの fingerprint と完全一致する
census を再生成できる保証はない。この fingerprint は sha256 の不一致検知（「同じ発話集合か」の
確認）にのみ使う。完全な再現性が必要な場合は `a0_eval_set.jsonl`（86件フルテキスト）と
`a0_full_census.json`（9件フルテキスト+文脈）のみが対象（いずれも§2.4のとおり git 管理外）。

### 2.6 再実行コマンド（更新）

```bash
python3 scripts/bench/a0_capture_replay.py dump-sample --n 80 --seed 20260812
python3 scripts/bench/a0_capture_replay.py evaluate
python3 scripts/bench/a0_capture_replay.py census
python3 scripts/bench/a0_capture_replay.py population-fingerprint
```

**必須成果物**: `a0_capture_replay.py`（**repo固定・commit対象**） /
`a0_sample_dump.json` / `a0_eval_set.jsonl`（86件、`prior_assistant_text` persist済み） /
`a0_full_census.json`（9件、同上） / `a0_population_fingerprint.jsonl`（1,124件、本文なし）
——後半4件は**ローカル実体のみ・commit対象外**（§2.4のsha256台帳で識別性を担保）。

---

## 3. 独立ラベル評価集合（round2更新）

### 3.1 再ラベルの結果

新サンプリング方法により round1 とは異なる80件が抽出された。全86件を §3.2 の基準
（AI直前出力への訂正、または prospective guardrail）で再ラベルし、`prior_assistant_text`
（150行遡及・実測で取得できた分は原文をそのまま persist）を根拠として rationale に記載した。

**結果**: TP=10（うち9件は具体的なPRIOR文脈と対応、1件はPRIOR空だがガードレール型で許容）、
not_TP=76。TP率 = 10/80 = 12.5%（machinery 6件を除く。Wilson95% CI [6.9%, 21.5%]）。

### 3.2 ラベル基準（不変）

「AI の直前出力/挙動への訂正」（後方参照型）、または「今後 X しないで／常に Y して」という
prospective guardrail 指示。既存 `CORRECTION_PATTERNS` 自身の guardrail 型パターン
（`only-what-asked` 等）の定義に合わせた。

### 3.3 automated tool prompt 混入率（codex round2 [Must-5] 軽量版対応）

archive由来の母集団を live `UserPromptSubmit.prompt` と完全に等価だと証明する harness は
作らない（頭の裁定）。代わりに、2回の独立抽出で観測した automated prompt（`receipt` /
`ai-daily-report` PJ がスクリプトから API 経由で送る定型プロンプト）の混入率を報告する:

| 抽出 | n | automated_prompt数 | 比率 |
|---|---|---|---|
| round1サンプル（層化） | 80 | 5 | 6.25% |
| round2サンプル（単純無作為） | 80 | 1 | 1.25% |

**precision/recall への影響方向**: automated prompt はいずれの抽出でも候補パターン
（直して/修正して/訂正して/やめて系）にヒットしていない（0/6件）。したがって:
- **precision への影響なし**（候補ヒット・census 9件はいずれも人間発話。automated prompt の
  混入は precision の分母・分子どちらにも寄与しない）
- **母集団の真の修正発話率（recall の外挿分母）をわずかに過小評価する方向**に働く
  （automated prompt は定義上「AIへの訂正」になり得ないため、これが母集団に混じるほど
  TP率の分母が水増しされ、真の人間対話に限定した場合の TP 率は今回の推定値
  （12.5%または13.75%）よりわずかに**高い**可能性がある——外挿母集団はやや過小、
  したがって合成 recall 点推定（≈5.0%）はやや**過大**評価の可能性がある）
- **A0 の結論（低リスク・低recallの小さな改善）を左右しない**方向の誤差である

---

## 4. 候補パターンの評価結果（round2再測定）

候補: `naoshite-request`（`(?<!見)(?<!作り)(?<!書き)(?<!考え)(?<!やり)直して|修正して|訂正して`）+
`yamete-request`（`やめて(ほしい|ください|くれ)`）。既存28パターン・アンカー・500字カットオフは無改修。

### 4.1 Precision — 固定窓・全数 census（n=9、文脈persist済み・Wilson95% CI付き）

| # | PJ | パターン | 実文 | **persist された直前AI発言（先頭150字）** | 判定 |
|---|---|---|---|---|---|
| 1 | amamo | naoshite-request | `会話ログさぐってみて。...これを何度も修正してもらった。` | 「スキルがおすすめです。理由: 内容がHTML資料を作るときの品質チェックリスト…」（skill/agent設計の議論・**話題不一致**） | **not_TP** |
| 2 | amamo | naoshite-request | `直して` | 「かからない見込みです。この『2〜3週間』は…前倒しで達成済みです。」（**話題不一致だが単語自体が明示的imperative**） | **TP**（弱：文脈不一致を認識の上、命令形自体を言語学的根拠とする） |
| 3 | amamo | naoshite-request | `修正して再配信して。` | 「SECOND OPINION (codex): 判定はNo-Goです。…認証情報の扱いに危険な説明があり、期待レスポンス例にも不正なJSONが含まれます。配布前にP0を直すべきです。」 | **TP**（強：指摘への直接対応） |
| 4 | evolve-anything | naoshite-request | `本PRで直しておいて` | 「4件起票し、PR #310にレビュー結果のサマリをコメントしました。｜issue｜内容｜実害｜…」 | **TP**（強：起票済み欠陥の修正指示） |
| 5 | zundamon-explainer | naoshite-request | `見つけた2件も直して` | 「…私が見つけた2件（『11回』の辻褄／つむぎの『そう。』の反復）は指示があり次第すぐ直せます。」 | **TP**（最強：AI自己申告への直接応答） |
| 6 | evolve-anything | naoshite-request | `P1・P2 とも実バグだね。修正して` | 「SECOND OPINION (codex): …#313に未解消の沈黙モードが2件あります。P1 — 一部dirだけ失敗したPJの失敗が最終サマリから消える」 | **TP**（強：codex指摘の追認+修正指示） |
| 7 | evolve-anything | yamete-request | `なんで、matsukaze-mindenでコメントしちゃったの、、、やめてほしい` | （`/clear` 直後でPRIOR空。ただし文中のPR#310コメントURLが外部の実在証拠） | **TP**（外部証拠） |
| 8 | sys-bots | naoshite-request | `4 background agents were stopped by the user: "...修正してください。"` | （machinery通知本文自体） | **not_TP（machinery）** |
| 9 | updater-index | naoshite-request | `訂正しておいて` | 「ファクトチェック完了。**4件中2箇所が誤りでした**（どちらも私の文面側の誤り）。」 | **TP**（最強：AI自己申告への直接応答） |

**Precision = 7/9 = 77.8%（Wilson 95% CI [45.3%, 93.7%]）**。round1と同数だが、
今回は9件全件の判定根拠が persist された実文脈で追跡可能（#1・#2 は「話題不一致」という
限界も含めて明示）。

### 4.2 `_MACHINERY_MARKERS` 追加後（推奨構成、不変）

`_MACHINERY_MARKERS` に `"background agents were stopped by the user"` を1行追加すれば
#8 が `should_include_message` の段階で除外される。**Precision = 7/8 = 87.5%
（Wilson 95% CI [52.9%, 97.8%]）**。

### 4.3 Recall — 独立ラベル評価集合（round2、n=80、machinery 6件は除く）

母集団中の真の修正発話率: **TP率 = 10/80 = 12.5%（Wilson 95% CI [6.9%, 21.5%]）**。
母集団 1,124件への外挿: **推定真の修正発話数 ≈ 140.5件（[78件, 242件]）**。

候補パターンがこのサンプル中の真陽性10件のうち何件を拾えたか: **Recall = 1/10 = 10.0%
（Wilson 95% CI [1.8%, 40.4%]）**。捕捉できたのは `訂正しておいて`
（census #9 と同一発話がサンプルにも偶然含まれた）。

**FN（見逃し）= 10 − 1 = 9件**（codex round2 [Should-new] 指摘の算術矛盾を修正）:
`あぁ、まだ一般公開する前でしょ？` / `調査に時間かけたら…ブロッカーにしないでね` /
`一覧みれるだけで値の更新は？…` / `要約も改行がなくわかりずらい` /
`あくまで子供がみるものだからね` / `なんで削除しちゃった？` /
`next-serverがなんでこんな立ち上がってる？` / `前回って7月29日なのよ` /
`右下にずんだもんが表示されない` ——いずれも「直して/修正して/訂正して/やめてほしい」の
**いずれの語彙も含まない**訂正表現。

**素朴な合成 recall 点推定**: census真陽性7件 ÷ 外挿母集団140.5件 **≈ 5.0%**
（§3.3 のとおり、この推定はやや過大評価の可能性がある方向の誤差を含む）。

---

## 5. 統計設計まとめ（round2更新）

| 指標 | 分子/分母 | 点推定 | Wilson 95% CI |
|---|---|---|---|
| Precision（census） | 7/9 | 77.8% | [45.3%, 93.7%] |
| Precision（machinery除去後） | 7/8 | 87.5% | [52.9%, 97.8%] |
| 母集団の真の修正発話率 | 10/80 | 12.5% | [6.9%, 21.5%] |
| Recall（サンプル直接測定） | 1/10 | 10.0% | [1.8%, 40.4%] |
| Recall（census⇔sample 合成点推定） | 7/140.5 | ≈5.0% | 不明（複合不確実性） |
| eval_set 内 precision（machinery除去前） | 1/2 | 50.0% | [9.5%, 90.5%] |
| eval_set 内 precision（machinery除去後） | 1/1 | 100.0% | [20.7%, 100.0%]（n=1で参考値） |

---

## 6. `_MACHINERY_MARKERS` 追加設計（不変）

`scripts/lib/rl_common/detection.py` の `_MACHINERY_MARKERS` へ1行追加:

```python
_MACHINERY_MARKERS = (
    "this session is being continued from a previous conversation",
    "base directory for this skill:",
    "stop hook feedback:",
    "caveat: the messages below were generated",
    "background agents were stopped by the user",  # 追加（§4.1 #8）
)
```

---

## 7. 副作用と監視設計

### 7.1〜7.2（不変）

hookの1ヒットは `corrections.jsonl` へ直接書込み（`source="hook"`）。weak_signals/llm_judge
レーンは経由しない——朝のy/n確認対象・LLM呼び出し費用は増えない。増えるのは
`capture_rate`/`outcome_metrics`/trigger発火などcorrections consumer側のみ。

### 7.3 監視指標とロールバック閾値（codex round2 [Must-new] 単位統一）

**指標は precision（TP/(TP+FP)）に統一する**（FP率とprecisionを混在させない。前版は
「FP疑い率」という指標名と「精度下限52.9%を下回ったら」という閾値を同一行に書いており、
FP率の悪化方向（上昇）とprecisionの悪化方向（下降）が逆であるにも関わらず表記が矛盾していた）。

| 指標 | 導入前ベースライン | 監視方法 | ロールバック閾値 |
|---|---|---|---|
| `source="hook"` の corrections/日 | 3か月で1件（実質0） | `capture_rate.py` のsource別集計（§8） | — |
| 週次サンプルレビューの **precision**（TP/(TP+FP)、最大10件/週を人手ラベル） | 未測定（母数0） | 週1で新規hookレコードをサンプルレビュー | **precision が2週連続で Wilson 下限 52.9%（§4.2基準線）を下回ったら該当パターンを一時無効化** |
| `trigger_engine` 発火頻度 | 導入前の閾値到達頻度（要別途実測） | trigger発火ログ | 導入前比 +50% 超で閾値見直しを検討 |
| auto-memory 直近5件ゲート内の hook由来比率 | 0% | ゲート内訳ログ | 持続的に高くFPが混入する場合はA3相当を前倒し |

上記はA0導入後最初の2週間限定の観測タスクとして運用する（恒常的な新規observability
builder追加ではないため凍結に抵触しない）。

### 7.4 harness と本番 hook 経路の乖離（制約として明記・頭の裁定#8）

live 等価性を証明する harness は作らない（頭の裁定）。かわりに、乖離が**どこに**残るかを
明記する:

- **検証済みで乖離がない部分**: `should_include_message` / `detect_correction` は本番と
  同一関数を monkeypatch 経由でそのまま呼ぶ（§2.2）。FALSE_POSITIVE_FILTERS・FPハッシュ除外・
  疑問符終端バイパスを含む判定ロジックそのものに乖離はない
- **未検証で乖離が残る部分（テキスト抽出経路）**: 本番 hook は `event.get("prompt")`
  （UserPromptSubmitイベントの生テキスト）を直接受け取るのに対し、本 harness は
  `utterance_archive.extractor` が transcript から抽出・整形した `text` 列を使う。
  抽出ロジックの違い（複数 content block の結合方法・`long_paste`/`excluded_pj` の
  事前除外・resume 時の重複排除）は本番 hook のインライン処理と完全一致する保証がない
- **観測可能な代理指標**: この乖離の実害は §3.3 の automated tool prompt 混入率
  （1.25%〜6.25%、2回の独立抽出）として部分的に観測できる——archive 経由の母集団に
  「本番 hook では単発 CLI 呼び出しとして別処理されうる」プロンプトが混入している。
  影響方向は §3.3 のとおり recall 側をやや過大評価する方向であり、A0 の低リスク判定を
  覆すほどの規模ではない
- **今後の検証手段**: A0 導入後、実際の `source="hook"` レコード（§8 の source フィルタで
  正しく分離される）を週次サンプルレビュー（§7.3）で目視することで、この乖離が
  precision に実害を与えていないかを継続的に確認できる

---

## 8. `capture_rate.py` の source×pattern_version 分離（頭の裁定#3・新設）

### 8.1 既存バグとの合流（ADR §2.6-5）

ADR-054 §2.6 は「audit の Correction Capture が `source` を見ずに channel 名を `hook` と
決め打ちしている（実際は `reflect_confirmed`）」ことを**既知の「嘘をつく数字」**として
記録済み。`scripts/lib/audit/sections_capture.py:186-188` の

```python
channel_line = (
    f"channel 別: hook {captured} 件（capture 率 {rate:.0%}）/ "
    f"llm_judge {llm_judge} 件（当PJ・weak_signals レーン・昇格前）"
)
```

の `captured`/`rate` は `capture_rate.compute_capture_rate()` が返す値で、**現状は
`corrections.jsonl` の `source` フィールドを一切見ずに machinery 以外の全レコード
（`reflect_confirmed`・`backfill`・`hook` 全部）を合算している**——「hook N件」という
ラベルが実態と食い違う唯一の呼び出し元がこのセクションであることを確認した（grep で
`compute_capture_rate` の呼び出し元は `sections_capture.py` の1箇所のみと確認済み）。
**pattern_version の段差問題（Must-9）とラベル不整合問題（§2.6-5）は同じループの同じ
1箇所で同時に直せる**（頭の裁定どおり、A0に含めるのが最も安い）。

### 8.2 実装差分（設計）

`scripts/lib/capture_rate.py::compute_capture_rate()` の該当ループ
（現行 126-137行、machinery除外の直後）:

```python
corrected_sessions: set = set()
hook_pattern_version_counts: Dict[str, int] = {}  # 新規
machinery_excluded = 0
for rec in _load_jsonl(Path(corrections_file)):
    if project is not None and not _project_match(rec, project):
        continue
    ts = rec.get("timestamp") or rec.get("ts") or ""
    if ts and ts < cutoff:
        continue
    if is_machinery_prompt(str(rec.get("message") or "")):
        machinery_excluded += 1
        continue
    # 新規: source でフィルタ（ADR §2.6-5 の「source を見ず channel を hook 決め打ち」
    # バグの根治。呼び出し元 sections_capture.py:186 の「hook N件」ラベルが実際に
    # source="hook" のレコードだけを指すようにする）
    src = rec.get("source") or "unknown"
    if src != "hook":
        continue
    sid = rec.get("session_id") or ""
    if sid:
        corrected_sessions.add(sid)
        pv = rec.get("pattern_version")  # A0 で hooks/correction_detect.py が新規追加するフィールド
        pv_key = str(pv) if pv is not None else "pre_pattern_version"
        hook_pattern_version_counts[pv_key] = hook_pattern_version_counts.get(pv_key, 0) + 1

captured = len(active & corrected_sessions)
...
return {
    ...,
    "hook_pattern_version_counts": hook_pattern_version_counts,  # 新規
}
```

**影響範囲**: 呼び出し元は `sections_capture.py` の1箇所のみ（grep で確認済み）。
`captured`/`rate` の値は**下がる方向に変わる**（reflect_confirmed 142件が分子から除かれる
ため）——これは意図された修正であり、ADR §2.1 の実測（hook由来は3か月1件）と整合する
正しい値になる。**表示文言はそのまま**（`"hook {captured}件"` は元々そう名乗っていたので
コード変更なし、意味が正しくなるだけ）。

`hook_pattern_version_counts` は当面表示に使わない（返り値に追加するのみ）。将来
`sections_capture.py` に「A0導入日以降/以前」の内訳行を追加する余地を残す設計とし、
本PRのスコープは capture_rate.py の1関数改修に留める（表示追加は別PRでもよい）。

### 8.3 writer側の対応する変更（`hooks/correction_detect.py`）

record に `pattern_version` フィールドを追加（初期値1）。§13の想定差分規模に計上済み。

---

## 9. `prev_action` データ欠損（既知の副次的発見・A1への申し送り確定）

§3.3 と同様、`prev_action` は `EXTRACTOR_VERSION=2`（#323, 2026-08-04）以降に取り込まれた
行で一律 `None`（本コーパス窓では 0/1,124件）。原因未特定。**A0 のスコープ外と確定した**
（頭の裁定）——本設計では代替として raw transcript から直前 assistant 発言を都度取得する
方式（§2.2）で代替済みであり、A0 の実装はこの欠損の修復を前提にしない。

**A1 への申し送り事項（head が ADR-054 §5-A1 に反映する）**: `prev_action` が
`EXTRACTOR_VERSION=2` 以降で一律 `None` になっている根本原因の調査を、A1（sidechain記録層
根治・再抽出migration）の設計スコープに含める。`_format_prev_action` / `pending_tool_names`
蓄積ロジック自体は #323（`b8f61672`）で変更されていないため、原因はインクリメンタル ingest
の挙動（`ingest_state` のオフセット単位の部分読み込みが `pending_tool_names` の蓄積開始点に
与える影響）にある可能性があるが未検証。

## 9.2 複合動詞の負例（codex round2 [Should-3]・頭の裁定#6で確定）

`(?<!見)直して` を **`(?<!見)(?<!作り)(?<!書き)(?<!考え)(?<!やり)直して`** に拡張した
（harness `candidate_vocab_addition()` に実装済み）。現コーパスでは実測0件だが、将来の
FP を構造で防ぐ（未決のまま残さない・P7）。

固定評価集合（regressionテスト、実コーパス由来でない合成負例。P8 の趣旨は実測での完了判定に
適用され、regressionガードの合成テストケースとは別枠）:

```python
# hooks/tests/test_correction_detect.py::TestNewPatterns に追加
NEGATIVE_COMPOUND_VERBS = ["作り直して", "書き直して", "考え直して", "やり直して"]
# いずれも naoshite-request にマッチしない（= detect_correction が None を返す）ことを確認
```

---

## 10. テスト方針（不変部分は前版と同一）

### 10.1 影響を受ける既存テスト（grep実測・不変）

```bash
rg -l "CORRECTION_PATTERNS|should_include_message|detect_correction|handle_user_prompt_submit" \
   hooks/tests scripts/lib/tests
```

direct: `test_correction_detect.py` / `test_hooks_safety.py` / `test_hooks_worktree.py` /
`test_e2e_correction_flow.py`。indirect: `test_capture_rate.py`（コメント言及のみ、ただし
**§8の変更により direct 化**——`compute_capture_rate` の `source` フィルタが `capture_rate` の
既存テストの fixture データ（`source` フィールド未設定のレコードを含む場合)に影響しうるため、
実装時に `test_capture_rate.py` の fixture が `source="hook"` を明示しているか確認が必要）。
無関係: `test_pitfall_injector.py`（別モジュールの同名関数）。

### 10.2 テストケース追加

- 真陽性（実コーパス由来、§4.1 で TP と判定した実文）: `"直して"` / `"修正して再配信して。"` /
  `"本PRで直しておいて"` / `"見つけた2件も直して"` / `"訂正しておいて"`
- 既知FP回帰防止: `"...評価ロジックも見直して"` / 複合動詞4件（§9.2）
- `_MACHINERY_MARKERS` 追加分の回帰テスト
- **新規（§8対応）**: `test_capture_rate.py` に `source` 別内訳のテストケースを追加
  （`reflect_confirmed` レコードが `captured_sessions` に混入しないことを確認する回帰テスト）

### 10.3 harnessをCIに載せるか（不変）

載せない（`utterances.db` はマシンローカル非決定論的データのため）。`a0_eval_set.jsonl` に
対する `evaluate` の実行は決定論的なため、`CORRECTION_PATTERNS` 変更時の手動regression確認に
使う運用を推奨。

---

## 11. 凍結非抵触の再確認（不変）

`_MACHINERY_MARKERS` 追加・`CORRECTION_PATTERNS` 2キー追加・`pattern_version`/
`hook_pattern_version_counts` フィールド追加はいずれも既存辞書・既存関数への追加で、
`shrink_freeze.py` の凍結4集合（store/observability builder/advisory adapter/weak-signal
channelの**新設**）に該当しない。

## 12. ADR本体の更新（頭が実施・不変）

`docs/decisions/054-four-pillars-completion-design.md` §5-A0 を: (1) アンカー緩和・
500字超見直しの2方針を実測に基づき不採用と明記 (2) 語彙追加のみを新方針として記載、
完了条件を§5の実測値ベースに更新 (3) `_MACHINERY_MARKERS` 局所対応で A2 依存を代替した旨を
明記 (4) `prev_action` データ欠損（§9）を A1 の調査事項に追記。

---

## 13. 想定差分規模（round2更新）

| ファイル | 変更種別 | 想定行数 |
|---|---|---|
| `scripts/lib/rl_common/detection.py` | `CORRECTION_PATTERNS` に2エントリ追加（複合動詞除外込み）+ `_MACHINERY_MARKERS` に1要素追加 | 約20〜25行 |
| `hooks/correction_detect.py` | record へ `pattern_version` フィールド追加 | 約2〜3行 |
| `scripts/lib/capture_rate.py` | `compute_capture_rate()` に `source=="hook"` フィルタ + `hook_pattern_version_counts` 追加（§8） | 約10〜15行 |
| `hooks/tests/test_correction_detect.py` | TP・FP回帰・machinery回帰・複合動詞負例4件 | 約45〜65行 |
| `scripts/lib/tests/test_capture_rate.py` | source別内訳の回帰テスト追加 | 約15〜25行 |
| `scripts/bench/a0_capture_replay.py` | 新規・commit対象（harness本体、分析専用） | 約440行 |
| `scripts/bench/a0_*.jsonl` / `a0_*.json`（4件） | 新規・**commit対象外**（`.gitignore`済み、生発話を含むためローカル実体のみ。§2.4のsha256台帳で識別） | — |
| `.gitignore` | `scripts/bench/a0_*` 4件を追加（頭が実施済み） | 数行 |
| ADR-054 本体 | §5-A0 の方針改訂（頭作業） | 数十行 |

**実装対象は 3ファイル改修 + 2ファイルテスト追加、約95〜135行程度。**

---

## 14. 完了条件（Precision Wilson下限50%閾値は頭が承認済み）

1. `census` を実装後の `CORRECTION_PATTERNS` に対して再実行し、新規ヒット全件を目視ラベル。
   Precision（Wilson 95% CI 下限）が 50% を下回らないこと（基準線 87.5% [52.9%, 97.8%]。
   **この閾値は頭の裁定で承認済み**）
2. `_MACHINERY_MARKERS` 追加により既知FPが新規ヒットに再出現しないこと
3. ADR headline実例が検出されることを個別確認
4. **recallの低さ（≈5.0%）は既知の限界として文書化する**ことをもって許容
5. `capture_rate.py` の `source="hook"` フィルタ導入後、`test_capture_rate.py` の既存テストが
   fixture の `source` 明示不足で誤って壊れていないか確認（§10.1）
6. `python3 -m pytest` exit 0

---

## 15. 未決事項

**なし（頭の裁定によりすべて確定済み）。**

- `prev_action` 欠損はA0スコープ外・A1への申し送り事項として §9 に確定記載した
- §7.3 監視指標の実施主体は**手動運用に留める**（daily runner には組み込まない——新設凍結・
  #379 Step1 に触れないため。頭の裁定）
- §14 完了条件のPrecision Wilson下限50%閾値は**承認済み**
- `hook_pattern_version_counts` は**本PRでは返り値追加のみに留め、`sections_capture.py` への
  表示配線は行わない**と確定した（§8.2 のスコープ限定どおり。表示追加は将来必要になれば
  別issueで検討する、が本設計の完了条件には含めない）

設計は本版で確定とする。実装フェーズへ移行可能。
