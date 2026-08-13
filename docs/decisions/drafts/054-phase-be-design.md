# ADR-054 Phase B / E 設計 — 朝の提示の是正（B2）+ judge 母集団の是正（B3-1）+ accept 記録の機械化（E1）

- Status: **Confirmed（設計確定・実装着手可）**。codex 2巡（round1 [Must]6/[Should]6/[Nit]4 →
  round2 差分レビュー [Must]4/[Should]3/[Nit]1）+ tacchi 1巡（条件付き着手可）で全 [Must] 反映済み。
  レビューは2巡で打ち切り（rules/design-review-gate: レビュアーの指摘が全て具体的な修正指示の形で
  解釈の余地がないため、3巡目は手続きのための手続きになる）。
- Date: 2026-08-13（v1〜v3）/ v4（本文書。codex round2 + tacchi 反映後の最終版）
- 対象 repo: このリポジトリ（evolve-anything）
- 上位: [ADR-054](../054-four-pillars-completion-design.md) §5 Phase B / Phase E、§6 実施順、§9.3
- 関連 issue: #379（縮小・CLOSED）/ #400（全PJ一括の対話 evolve 入口）/ #401（戦果ボードの週1導線）/
  #402（1コマンド revert・マージ済み）/ **#442**（B3-1: judge 母集団の是正）/
  **#443**（B2: 朝の提示の是正）/ **#444**（E1: accept 記録の機械化）/
  **#445**（corrections ストアの入力衛生）/ **#446**（reject された提案が次回 emit で再提示される）/
  **#447**（`similarity.tokenize` が日本語を分割できない）
- 実測日: 2026-08-13（全て read-only。DuckDB は `read_only=True`、ストアの sha256 不変を確認）

> 本文書は ADR-054 の Phase B（朝の提示の質）と Phase E（correction → accept の変換経路）を
> 実コード・実データの再実測に基づいて具体化した設計文書。判定と行動は上位 ADR-054 §5 Phase B /
> Phase E に反映済み（2026-08-14 rev3）。本文書はその設計根拠・実測値・レビュー反映の詳細を保持する。

---

## 判定と行動（先に結論）

| | 判定 | 理由 |
|---|---|---|
| **B3-1** judge 母集団の是正 | **やる**（PR1） | 未判定在庫の 28.2% が tracked 外。同じ費用で実効スループットが上がる |
| **B2** 朝の提示（machinery 除去 + 順位と打ち切りの分離） | **やる**（PR2） | **朝の候補 300件のうち 47件（15.7%）が委譲メッセージ**。順位を直す前にこれを塞ぐ |
| **E1** accept 記録の機械化 | **やる**（PR3） | `learning_skill_md_must_not_enforcement` と同型の既知欠陥の根治。E2 の成否と独立 |
| **E2/E3** correction → skill diff の変換経路 | **やらない（凍結）** | 着手前ゲート E0 が不通過。材料が skill diff ではなく、反映先の rules 経路は reflect が既に持っている |

**柱3(b) について訂正**: 「E が止まるので当面測れない」は**過大主張だった**（tacchi 観点6）。
`_extract_candidates` には skill_evolve assessments という**既存の提案源が生きている**ので、
PR3（E1）で accept が機械記録されるようになれば、E2 なしでも accept は積み始められる。

---

## Context

ADR-054 のうち Phase 0 / A0 / A1第一段階 / A2 / A3 / A5 / C1 / C(a) / D(PR1,PR4) と G1 が完了し、
残るのが Phase B（朝の提示の質・柱2）と Phase E（correction → accept の変換経路・柱3(b) の前提）。
本設計は両者を実コード・実データの再実測に基づいて具体化する。

レビュー: **codex 1巡**（`設計修正要`・[Must]6/[Should]6/[Nit]4）+ **tacchi 1巡**（`設計修正要`・条件付き着手可）。
両者の指摘と反映は §8。**両者が食い違った1点（E1 の可否）は §3 で頭が裁定した。**

---

## 1. 実測（2026-08-13・全て read-only。DuckDB は `read_only=True`、ストアの sha256 不変を確認）

### 1.1 朝の提示（Phase B）— 5点が判明

| # | 発見 | 根拠 |
|---|---|---|
| B-a | **朝の候補の 15.7% が委譲メッセージ**。`REVIEW_CHANNELS` の未昇格 300件のうち **47件**が `<teammate-message` を含む（rephrase 25 / llm_judge 22）。うち22件は 08-11 以降の検出。**`detected_at` 降順の上位10件中6件がこれ** | 実測。`is_machinery_prompt`（`rl_common/detection.py`）は **47/47 を捕捉できる** |
| B-b | **A2（PR #431）は書込側の修理**なので、**既に検出済みの在庫はそのまま残っている** | 同上（08-13 00:07 検出分にも混入） |
| B-c | **順位を直しても届かない候補がある**。digest 生成時に `build_review(max_groups=3)` で PJ ごと3件に切ってから global 化・既読差し引きをするので、4件目以降は順位規則の適用対象に入らない | `proposal_digest.py:262,290` / `daily_review.py:332`（codex [Must]2） |
| B-d | **global レーンが構造的に死んでいる**。per_pj を先に連結するので per_pj に未既読が2件あれば global は永久に出ない | `proposal_digest.py:343-347` + `:388-389` |
| B-e | **judge 未判定 10,419件のうち 2,942件（28.2%）が tracked 外 PJ**。tracked 外は判定枠を消費するが `proposals` に到達する経路が無い | `ingest.py:137,145` / `proposal_digest.py:283` / `fleet/cli.py:454-460` |

**順位キーの識別力が実データでほぼ無い**（tacchi 実測。設計の前提を覆した）

- `evidence.count` は**ほぼ全 group で 1** → 「再発回数」キーは実質機能しない
- `cross_pj_confirmed` は**今朝の全 group で空**（confirmed idiom は 131件あるが、正規化完全一致という
  照合の粗さで一致0）→ 「他 PJ で確認済み」キーも実質機能しない
- 結果、v2 の合成キーは**実質「新しさ」だけで並ぶ**。そこに B-a の委譲メッセージが乗る

**tracked 外の内訳（未判定件数）**

```
matsukaze-takashi 1017   ← home 起動セッション。PJ ではない → 除外で確定
rl-anything        800   ← 当 repo の旧 slug → alias fold で evolve-anything に畳む（確定）
zundamon-explainer 714   ← 実在 PJ。未登録 → ★ユーザー裁定
ai-office          271   ← 実在 PJ。未登録 → ★ユーザー裁定
その他13 slug      140   ← build/docs/js/chain/… = パス断片由来のゴミ slug → 除外で確定
```

**judge の需給**（ADR-054 §6 が要求していた「A1/A2 後の流入量再計測」への回答）

| | 全 PJ | tracked のみ |
|---|---|---|
| 週次流入（W27〜W32 平均） | 約 1,200 件 | 約 1,030 件 |
| 日次上限 200 × 7日 | 1,400 件 | 1,400 件 |
| **週あたりの余剰** | **約 +200** | **約 +370** |

→ **上限 200/日は既に流入を上回っている。B3 の解は「上限引き上げ」ではなく「母集団の是正」。**
W25〜W31 の judge 0件は daily runner 導入（2026-08-11）前の空白であり、バグではない。

### 1.2 Phase E — 着手前ゲート E0 が不通過

ADR-054 §5 Phase E は「経路が無いのか、経路はあるが起動しないのか」を実 repo の手動1周で確かめよ、
としていた。**静的解析 + 実コーパス実測で決着したので手動1周は実施しない**（ADR のゲート変更として ADR-054 §5 Phase E で裁定済み）。

**(1) レーンごとに答えが違う**（codex [Should]1 を受けて表現を厳密化）

| レーン | 判定 | 根拠 |
|---|---|---|
| corrections → **evolve の emit→drain skill-diff candidate lane** | **経路が無い** | `_candidates.py:79-125` の入力は discover の `matched_skills` と skill_evolve の `assessments` だけ。discover 側の入力は `usage.jsonl` / `errors.jsonl` / `optimize_history.rejection_reason`（`runner.py:186-200` 他） |
| corrections → genetic optimizer / evolve-loop の `collect_corrections` | **入力としては存在するが実質空** | 下記(2) |
| corrections → pitfall / hook candidate / instruction violation | 経路はあるが `matched_skills` に合流しない | `runner.py:365-455` |
| 過去10件の accept | **corrections 由来ではない** | 全件 `skill_evolve:medium`・`decision_source` 無し＝#376 以前の hash-proxy 誤検出。全件 invalidated |

→ ADR-054 §3.1 の「変換経路が無い」は **「emit→drain の skill-diff candidate lane には入らない」** に書き換え済み。

**(2) 入力が構造的に空になる機序**

```python
# optimize_core.py:79-83（consumer）
if record.get("reflect_status") == "applied": continue
last_skill = record.get("last_skill") or ""
if target_skill_name.lower() in last_skill.lower(): corrections.append(record)
```
```python
# correction_semantic/promote.py:277-288（現行の唯一生きている writer・152/162件）
out = {"correction_type": "semantic_idiom", "last_skill": None, "reflect_status": "applied", ...}
```
writer が consumer の2フィルタ**両方**に引っかかる値をハードコード。実データ 162件の `last_skill` は全件 `None`。

**(3) E0 の実測 — 通過条件 10件に対して 0件**

有効 corrections 155件の `message` を pattern text として `skills/*/SKILL.md` 23件に当てた結果、
`JACCARD_THRESHOLD = 0.15` を超えたものは **0件**（最大 0.0721）。

**理由は2つあり、両者は独立している**（tacchi 観点4）:

- **① 照合器が日本語を読めない（技術的欠陥・issue #447）** — `similarity.tokenize` は `re.split(r"[\s\W_]+")` で、
  日本語は `\w` に含まれるため分割されない。実測: 平均トークン長 8.5 文字。
  **この欠陥がある限り、内容が何であれ Jaccard は構造的に閾値を超えない。**
- **② 材料が skill diff ではない（本質的欠陥）** — corrections の中身は
  「PRじゃないの？」「もっとわかりやすく整理して確認して」「rule書き換えるだけじゃだめかなぁ？」＝
  **行動規範**。`correction_type` は `semantic_idiom 145 / stop 8 / iya 1 / naoshite-request 1`。
  さらに `[Image` 始まり 37件（23.9%）、`Stop hook feedback:` 8件、
  **assistant 自身の出力が semantic_idiom として登録されている行**もある（tacchi 実測。corrections ストアの
  入力衛生の問題。issue #445）。

**したがって E0 は「①と②を区別できない実験」だった**（tacchi）。ただし②は message を直接読めば
判定でき、**②単独で E2 を止める理由として十分**なので、①を直してから再実験する必要はない。

**(4) 既存の設計思想と正面衝突する**

```python
# _candidates.py:79-90（docstring）
# remediation の fix は target が rules/hooks/構造と異種で skill_quality 母集団の均質性を
# 壊すため対象外（ADR-041 follow-up の意図的スコープ）。
```

---

## 2. Phase B の設計

### PR1（issue #442）— judge の母集団を tracked_projects に絞る

**効果（2026-08-13 のユーザー裁定を反映して再実測。当初見積りから縮小した）**

| | 裁定前の見積り | **確定値（実測）** |
|---|---|---|
| judge 対象から外れる在庫 | 2,942件（28.2%） | **1,157件（11.1%）** |
| 残る未判定在庫 | 7,477 | **9,262** |
| 週次流入（W27〜W32 平均） | 1,030 | **1,168** |
| 週あたり余剰（上限 1,400/週 に対して） | +370 | **+232**（現状は +200） |

縮小した理由: `rl-anything`(800) は alias fold で残り、`zundamon-explainer`(714) と
`ai-office`(271) はユーザー裁定で tracked に追加したため。**実際に外れるのは
`matsukaze-takashi`(1,017) と13個のゴミ slug(140) だけ。**

> **効能の看板は「在庫解消週数」にしない**（tacchi [Should]5）。「52週 → 20週」は
> ①流入・上限一定 ②daily runner が毎日実際に走る（launchd 登録≠実行の前科あり）
> ③古い在庫を消化する方針を採る、を暗黙に仮定していた。③は下記の契約5で「cutoff 宣言」を
> 採るので、そもそも在庫解消週数は指標として意味を失う。

**PR1 の価値は、正直に書くと次の3点**（在庫解消の高速化ではない）:
1. **PJ でない発話（home 起動セッション）に LLM 費用を払わなくなる** — 1,157件 ≒ 判定枠 約6日分
2. **永久に届かない古い在庫を「対象外」と明示する**（契約5）。黙って腐らせるのをやめる
3. **除外の内訳が朝の通知に出る**（契約4）。silence != evaluated

**実装位置**: `judge_runner` が `query_utterances_all_projects` から受け取った**直後**に絞る
（同関数は「pj 照合をスキップする横断検索」という契約なので**関数側は変えない**。`fleet recall` 等に非影響 — codex [Should]2）。

**確定させる契約**:
1. **alias fold** — tracked config は絶対パスのリスト、utterance は slug。`rl-anything → evolve-anything` は
   既存の `pj_slug_match` 系の**同じ正規化関数を使う**（新実装しない）。
2. **処理順の固定** — `tracked filter` → `judged key 除外` → `unjudged_total` 算出 → `daily cap 選定`。
   ユーザーに見せる残件数は**絞った後**の値。
3. **除外 PJ の発話は `correction_judged.jsonl` に書かない** — 将来 tracked に追加されたとき通常の
   未判定として復帰できる。テストで固定する。
4. **除外の可視化（silence != evaluated）** — `run_daily_judge` の返り値に `excluded_untracked_total` と
   `excluded_untracked_by_pj` を追加し、**dry-run / run / lock-skip / source-failure の全分岐で返す**
   （codex [Should]3）。`daily/queue_notice.py` が既存の判定サマリ行に1文を足し、SessionStart の既存
   `judge_cap` 通知経路に載せる（新しい通知系統は作らない）。
5. **古い在庫の cutoff を宣言する**（tacchi 観点3）。仕様を確定させる（codex 2巡目 [Should]2）:

   | 項目 | 決定 |
   |---|---|
   | 判定式 | **発話時刻**（`utterances.timestamp`）が `now - N 日` **以降**なら対象。厳密に古いものは対象外（境界 `==` は対象に含める＝ TTL の `<` と揃える） |
   | N の既定値 | **90日**。userConfig `judge_utterance_max_age_days` で変更可（**既存 userConfig へのキー追加は凍結対象外**） |
   | 返却 | `excluded_before_cutoff_total` を **dry-run / run / lock-skip / source-failure の全分岐で返す** |

   **TTL 45日とは重複しない**（codex 2巡目 [Should]2）。cutoff は「未判定 utterance を judge に**入れるか**」、
   TTL は「判定後に生成された weak_signal を**提示するか**」で、**別段階・別時計**。
   むしろ cutoff が無いと、古い発話が今日 judge されて `detected_at` 基準の「新鮮な weak」として
   45日間提示され続ける（PR2-c のキー3を発話時刻にする理由と同根）。

   **正直な効果の見積り**: utterances.db の保持は現時点で約3ヶ月なので、**90日 cutoff は現在の在庫を
   ほとんど減らさない**。これは「将来 DB が長期化したときに無制限に古い発話へ LLM 費用を払わない」ための
   予防であって、**今の在庫 9,262件の問題を解決するものではない**。
   在庫は余剰 +232/週 で徐々に消化される（newest-first なので古い側は後回しだが、cutoff 内なら到達しうる）。

**日次上限は引き上げない** — ADR-054 §6 の「B3 は流入量を再計測してから」への答えは「**引き上げ不要**」。

### PR2（issue #443）— 朝の提示（machinery 除去 → 順位と打ち切りの分離 → 表示）

**PR2-a: read 時の machinery 除外を最初に入れる**（tacchi [Must]1・最優先）

A2 は書込側の修理なので、既に検出済みの 47件は在庫として残り続ける。
`provenance.text` に `is_machinery_prompt`（`rl_common/detection.py`）を当てて落とす。
実測で **47/47 を捕捉できる**ことを確認済み。

**除外を入れる位置**（codex 2巡目 [Must]1 — 早期案の `_read_new` 単独案は誤り）:

`_read_new` だけに入れると Step 6.2 と digest からは消えるが、**Step 6.1 の
`bootstrap_backlog._read_backlog` には machinery が残り、queue・observability と母集団が分裂する**。

- **既存5 reader の単一 predicate である `filter_actionable`（`correction_semantic/promote.py:125`）に入れる**
- 独自 reader の `_read_backlog`（`bootstrap_backlog.py:351`）も**同じ述語を通す**
- これは「同じ量に式を2つ作らない」「検出器間の矛盾は共有 predicate で解消」の既存判例と同軸

**除外件数の surface 契約**: `excluded_machinery_total` と
`excluded_machinery_by_channel` を digest・queue・observability の返り値に載せる。
黙って減らさない（silence != evaluated）。

- 新設ゼロ（既存述語の適用箇所を増やすだけ・既存 dict へのキー追加）
- **判定は `is_machinery_prompt` を単一ソースとする**（文字列 allowlist を新設しない・P2）

**PR2-b: 順位と打ち切りを分離する**（codex [Must]2）

現行は `build_review(max_groups=3)` で PJ ごとに切ってから global 化・既読差し引きをするため、
**順位規則をどう変えても4件目以降は候補に入らない**。

```
現行: PJごとに順位付け → 3件に切る → global 抽出 → 既読差引き → 先頭2件
改訂: PJごとに順位付け → 切らずに全候補を集める → global 抽出 → 既読差引き
      → 最終集合に composite sort を一度だけ適用 → 先頭2件
```

**signature まで固定する**（codex 2巡目 [Must]2 — 「分離した呼び出し口」は既存 API に**存在しない**。
`build_review` は必ず `groups[:max_groups]` するので、巨大値を渡す方式では全件契約にならない）:

```python
# correction_semantic/daily_review.py
def build_review(..., max_groups: Optional[int] = 5, ...):
    ...
    top = groups if max_groups is None else groups[:max_groups]
```

- **既定値は 5 のまま**なので evolve Step 6.2 の既存呼び出しには影響しない
- **digest 側だけが `max_groups=None`（無制限）で呼ぶ**
- `None` 無制限化と `build_ranked_review()` 新設の二択のうち、**前者を採る**（新関数を増やさない・#379）

**「二重ソートは冪等」という主張は撤回する**（v2 での誤り）。

**PR2-c: composite sort のキーと計算契約**

実データでキー1・キー2の識別力がほぼ無いこと（§1.1）を踏まえ、旧案から定義を変えた:

| 順 | キー | 定義 | 代表値の決め方 |
|---|---|---|---|
| 1 | PJ 横断で見えているか | **`cross_pj_confirmed` が非空 または global レーン所属**（tacchi [Must]2 — 旧案は両者を同一視していたが別物。confirmed は「他 PJ で human が y を押した idiom と一致」、global は「idiom テキストが2 PJ 以上で**観測**された連結成分」） | bool。global 所属は `_extract_global_groups` の結果を使う |
| 2 | 再発回数 | `evidence.count` | int。欠損は 0。**実データでは大半が 1 なので実質 tie-break** |
| 3 | 鮮度 | **発話時刻**（`utterances.db` を `source_path:line_no` で read 時 join。**`detected_at` は判定時刻であって発話時刻ではない** — tacchi [Should]。PR1 で在庫消化が進むと「4ヶ月前の発話が昨日 judge されたのでトップ」が起きる） | group 内の `max`。join 失敗時は `detected_at` にフォールバックし、その旨を group に記録 |
| 4 | 決定論の担保 | `min(signal_keys)` | 文字列。`signal_keys` は list なので最小値に畳む（codex [Must]1） |

**既読差し引き後の再計算を可能にするデータ形**（codex 2巡目 [Must]3 — 従来案の書き方では**実装不能**。
`_slim_group` は group 集約後の `count` しか保持せず、各 `signal_key` の発話時刻・confirmed 状態を失うため、
既読キーを除いても `max(発話時刻)` を再計算できない）:

digest の各 group に **`signal_meta_by_key`** を持たせる。

```python
"signal_meta_by_key": {
    "<signal_key>": {"uttered_at": "...", "detected_at": "...", "cross_pj": [...]},
    ...
}
```

既読差し引き後は残存キーだけから再計算する: `count = len(remaining_keys)` /
`max(uttered_at)` / `min(remaining_keys)` / キー1 は残存キーの `cross_pj` の和。

**鮮度 join のバッチ設計**（codex 2巡目 [Should]1 — PJ ごと・group ごとに `query_utterances` を呼ぶと
全 DB 走査が反復される）:

- `build_proposal_digest`（daily runner 内）で **全 PJ を一度だけ read し、
  物理PK `(source_path, line_no)` → `timestamp` の map を作る O(U+S) の一括方式**にする
- SessionStart は既にできた digest を読むだけなのでホットパスのコストは増えない
- **失敗を4種に区別して digest に載せる**: DB 不在 / DuckDB 不在 / query 例外 / 個別キー不一致。
  それぞれの件数と、`detected_at` へフォールバックした件数を出す

**PR2-d: 提示文に判断材料を戻す**

`cross_pj_confirmed` だけを足す旧案は、**今朝の実データで発火0件＝実質 no-op**（tacchi 観点2）。
実際に朝の y/n に欠けているものを出す:

```
3週間前の発話 ・ 他2PJでも同種の指摘（amamo, figma-to-code）
```

- **発話の実時刻**（相対表記）— judge ラグがある以上、古い文脈の指摘に y を押させない安全弁
- **観測ベースの cross-PJ**（`reps_by_pj` は既にある）— confirmed 一致より遥かに発火しやすい。
  confirmed の場合はより強い文言にする
- channel 名（`llm_judge` / `rephrase`）は**出さない** — ジャーゴンであり判断材料にならない

**PR2-e: 契約テスト**（ADR-054 §8 の B 完了条件 + 両レビュー）

固定 corpus（複数 PJ + global group + 既読混在）で:
- sidechain 由来 0件 / **machinery 由来 0件** / content-rich チャネルからの供給がある
- 既読差し引き後も4キーの順序が保たれる
- **per_pj に2件ある状態で global group が提示に到達できる**（B-d の回帰防止）
- **PJ ごと3件で切られていた4件目が、順位上位なら提示に到達する**（B-c の回帰防止）
- **キー1〜3 が全て同値**の fixture で順序が決定論（キー4で確定）。さらに
  **既読差し引き後の残存キーによって順序が変わるケース**まで固定する（codex 2巡目 [Nit]）
- 鮮度 join の失敗4種（DB 不在 / DuckDB 不在 / query 例外 / キー不一致）でフォールバックし、
  その件数が digest に出る
- global group が複数 `signal_keys` を持つ / 既読差し引きで代表 PJ が消える（codex [Nit]2）
- **実ストア dry-run で「上位2件に machinery 0件」を検査する1本を追加**（tacchi 追加要求。
  合成 fixture だけでは §1.1 の発見を再現できない・P8）

### やらないこと（記録）

**digest のスナップショット固定** — `build_proposal_digest` は daily-run からしか呼ばれないので、
日中に発生した weak は当日の提示に出ない。「朝出した案が昼に入れ替わる」のは体験として別の設計判断なので、
**#400（一括入口）の設計と一緒に決める**。既知の制約として記録に留める。

---

## 3. Phase E — E1 はやる / E2・E3 は凍結

### 頭の裁定: E1（accept 記録の CLI 化）は実施する

**ここは codex と tacchi が正面から食い違った唯一の点**なので、根拠を明記する。

- **codex [Must]5**: `evolve --drain --accepted <id>` を agent が任意に実行できると、その CLI 呼出し自体が
  「人間が y を押した」根拠になる。現行の inline python MUST と**同じ信頼境界**を、より呼びやすい CLI に
  移しただけ → 見送るべき
- **tacchi 観点6**: `learning_skill_md_must_not_enforcement` の既知欠陥類型の**根治**であり、
  E0 の結果と無関係に価値がある → やるべき

**裁定: やる。両者の指摘は排他ではない。**
現行の inline python も**実行するのは Claude** なので、信頼境界は既に「Claude が対話の結果を受けて実行する」。
CLI 化しても境界は**悪化しない**（codex の指摘は「E1 は承認 provenance の問題を解決しない」であって
「E1 が問題を悪化させる」ではない）。一方で E1 は「実行され損ねて accept が記録されない」という
**別の実害を確実に消す**。したがって実施し、**codex [Must]5 の要求を E1 の設計要件として全て取り込む**:

1. accepted/rejected ID は**直前の対話結果からどう受け渡すか**を SKILL.md に明記する
2. **既知の非対話 call site（hook / daily runner / `--auto` 系）が decision 引数を渡さないことを
   テストで固定する**。
   ※「機械的に保証する」は**過剰約束だったので撤回**（codex 2巡目 [Must]4）。
   `collectors.py:234-235` の不変条件は「hook 自身が渡さない」という呼び出し規約にすぎず、
   **CLI は呼出元を認証できない**（daily runner でも hook でも任意プロセスでも同じ引数を生成できる）。
   偽造不能な承認 capability を対話ホストが発行する仕組みは現状存在しない。
   必要なら**承認 token の発行・検証境界として別設計**にする（本 PR のスコープ外）
3. `--accepted` と `--rejected` の**重複指定・未知 ID・理由なし reject を拒否**する
4. synthetic E2E で「**applied だが accepted なしは deferred**」を固定する
5. 引数名は既存 optimizer の `--accept`/`--reject`（単数・別意味）と紛らわしいので、
   ヘルプに「proposal ID の複数指定」であることを明示する（codex [Nit]3）

### E2/E3（correction → skill diff）は凍結する

**理由は3つ**（tacchi 観点6 が最も明快なので採用）:

1. **材料テストが不通過** — 0/155。内容は行動規範であって skill 本文の diff ではない（§1.2(3)）
2. **反映先の経路は既にある** — corrections → CLAUDE.md / rules は **reflect が既に持っている責務**。
   E を rules 側へ「切り替える」のは **reflect の再発明**で、#379 が最も嫌う重複
3. **柱3(b) の分母は E2 なしでも作れる可能性がある** — `_extract_candidates` には skill_evolve
   assessments という既存の提案源が生きており（`skill_evolve/assessment.py:109` が未進化 skill に
   high/medium を生成し、`_emit.py` が pending 化する経路を codex が実コードで確認）、E1 で accept が
   機械記録可能になれば既存レーンだけで accept が積み始めうる。
   **「柱3(b) は E が前提」という当初の前提は、E1 と E2 を束ねたことによる過大主張だった。**
   ただし **「E1 導入後に必ず積み始める」とまでは言えない**（codex 2巡目 [Should]3 — 全 skill が
   `already_evolved` / `skip_llm_evolve` / batch guard に当たれば 0件）。
   **PR3 の完了後に実データで pending 生成件数を実測して確かめる**（実測するまで数字を主張しない）

**解凍条件**（凍結は永久ではない）: E1 導入後、**既存レーンで accept が積まれてもなお
「correction 由来の提案が欲しい」という要求が実際に観測されたら**再検討する。

### E をやるとしたら何が必要か（将来の別 ADR 用の保存）

codex [Must]3/4/6・[Should]5 は、将来 E をやる場合に必ず解く必要があるので保存する:

1. **provenance が下流に伝わらない** — `_enrich_patterns` が運ぶのは `type` / `pattern` / `matched_skill` /
   `skill_path` / `jaccard_score` だけ。correction identity は `_emit.py` に残らず N↔N 追跡が成立しない
2. **再提示の抑止が逆向き**（issue #446） — `proposal_id = (repo_id, relative_path, before_sha)` は「対象ファイルの
   現在世代」を指すので、**reject 後は同じ ID が次回も emit され**（emit は reject history を見ない）、
   **accept 後は before_sha が変わって新しい ID として再生成される**。
   これは corrections 由来に限らず現行 A レーン全体の性質
3. **correction → pattern の schema が未定義** — 何を `pattern` にするか、複数 correction の集約、
   `count` / `type` / `suggestion`、invalidated 判定、PJ alias fold
4. **合格判定は量だけでは弱い** — unique correction 数 / unique target skill 数 / pair 数 / score 分布 /
   人手 precision / 同一 skill への集中度を分けて測る
5. **`tokenize` が日本語を分割できない**（issue #447） — 照合方式そのものの変更が要る（§1.2(3)①）

---

## 4. 実施順と PR 分割

### 起票済み issue

本設計の成果は `todoroki-godai/evolve-anything` の issue に起票済み（`feedback_tech_eval_issue_gate`
の3問ゲート＝重複／作用／発動条件 を通したもののみ）:

| issue | 種別 | 内容 | 対応 |
|---|---|---|---|
| **#442** | 実装 | **judge 母集団の是正**（tracked 絞り込み + alias fold + cutoff 宣言 + 除外の可視化） | PR1 |
| **#443** | 実装 | **朝の提示の是正**（machinery の read 時除外 → 順位と打ち切りの分離 → 鮮度 join → 表示） | PR2。#400 に紐づく |
| **#444** | 実装 | **accept 記録の機械化**（`--accepted`/`--rejected` CLI + 非対話 call site のテスト固定 + E2E） | PR3。#401 / #402 に紐づく |
| **#445** | 欠陥 | **corrections ストアの入力衛生** — `[Image` 37件(23.9%) / `Stop hook feedback:` 8件 / assistant 自身の出力が `semantic_idiom` として登録されている | 記録のみ（§1.2(3)） |
| **#446** | 欠陥 | **reject された提案が次回 emit で再提示される**（emit は reject history を見ない）。A レーン全体の性質 | 記録のみ（§3「E をやるとしたら」2） |
| **#447** | 欠陥 | **`similarity.tokenize` が日本語を分割できない** — Jaccard を使う全経路（discover の enrich / suppression / episodic）に影響 | 記録のみ（§1.2(3)①） |

**単独 issue を起票しなかったもの**（既存 issue へのコメント追記で足りる・単独 issue にすると管理コストが上回る）:
`pitfall_manager` の `correction_type` allowlist・`optimize_core.collect_corrections` のデータ契約違反は
**#267（Epic: 接続漏れ）へのコメント**、`store_registry` の宣言 drift は**害が無い**ので記録のみ、
A1保留と PR1 の相互作用は **#442 の本文に注意点として含める**。

既存 issue への追記: **#400**（#443 が前提整備であること）/ **#401**（(b) 系は E2 なしでも
skill_evolve レーンで積みうると訂正）/ **#267**（E2 凍結の根拠）。

### PR 構成

```
PR1（judge 母集団の是正）──┐ 並行可（owned paths が重ならない）
PR2（朝の提示）───────────┘
PR3（E1: accept 記録の機械化）── 独立
```

| PR | 内容 | 主な変更ファイル | 目安 |
|---|---|---|---|
| 1 | judge 母集団を tracked に絞る（alias fold / 処理順 / 除外の可視化 / cutoff 宣言） | `correction_semantic/judge_runner.py`, `daily/queue_notice.py`, `bin/evolve-daily-run`, tests | 中 |
| 2 | machinery の read 時除外 → 順位と打ち切りの分離 → composite sort → 鮮度 join → 表示 → 契約テスト | `correction_semantic/daily_review.py`, `daily/proposal_digest.py`, tests | 大（2本に割ってもよい: 2a=machinery 除外 / 2b=順位と表示） |
| 3 | `--accepted` / `--rejected` CLI + 非対話経路の遮断 + SKILL.md の MUST 置換 + synthetic E2E | `skills/evolve/scripts/evolve/cli.py`, `skills/evolve/SKILL.md`, tests | 中 |

「PR3 と PR5 は書込境界が重なる」は codex [Should]4 のとおり誤りだったので撤回（該当 PR 自体が消えた）。

---

## 5. 検証

各 PR 共通:
```bash
python3 -m pytest -n 0 -q          # exit 0（件数は契約にしない）
bin/evolve-dogfood-gate --layer light
claude plugin validate .
```

**shrink-freeze の非抵触を PR ごとに明示確認する**（codex [Nit]4）— 新 store 0 / 新 observability section 0 /
新 advisory adapter 0 / 新 weak_signal channel 0。既存 dict へのキー追加（鮮度 / `cross_pj_confirmed` /
`excluded_untracked_*`）が `store_write` barrier と `test_shrink_freeze.py` の blocking contract を通ることを
実行して確認する。

Phase 固有:
- **PR1**: 実データ dry-run で「除外される 2,942件の内訳」「絞った後の未判定件数」「cutoff で外れる件数」を
  実測し、ストアの sha256 が不変であることを確認
- **PR2**: §2 PR2-e の契約テスト。**実ストア dry-run の1本を必ず含める**（単日の目視・合成 fixture のみでは
  完了としない）
- **PR3**: synthetic E2E で1周（emit → 適用 → `--accepted` → optimize_history の accept → `bin/evolve-revert`
  で戻せる）+ 失敗系（applied だが accepted なし＝deferred / 未知 ID / 重複指定 / 非対話経路からの拒否）

---

## 6. 関連 issue との紐づけ

| issue | 本設計との関係 |
|---|---|
| **#400** 全PJ一括の対話 evolve 入口 | **PR2 が前提整備**。#400 は「提案を順に y/n」の導線、PR2 は「その提案の順序と中身」。digest のスナップショット固定は #400 側で決める |
| **#401** 週1導線と取り下げ | **PR3 が (b) 系の分母を作る**。E2 凍結後も skill_evolve レーンの accept は積めるので、受け入れ条件は満たしうる（過去の「当面満たせない」を訂正） |
| **#267** Epic: evolve の接続漏れ | E2 凍結の根拠（材料が skill diff でなく、反映先は reflect が既に持つ）を Epic に記録 |
| **#379** 縮小・新設凍結 | 全項目が凍結4集合に非抵触。**E2 を止めることは縮小方針に沿う** |
| **#402** 1コマンド revert | PR3 で accept が積み始めれば revert 可能な entry が生まれる（現在0件） |

---

## 7. ユーザー裁定（2026-08-13 に確定・未決ゼロ）

古い在庫（PR1 の契約5 で cutoff 宣言に確定）と E0 不通過時の扱い（§3 で凍結に確定）は
設計側で決めた。残る1点をユーザーに確認し、**確定した**:

**tracked 外の実在 PJ の扱い → `zundamon-explainer` と `ai-office` を `tracked_projects` に追加する**
（ユーザー選択・2026-08-13）。現役 PJ の学習素材を捨てる理由がなく、追加しても週次流入は約 +170件で
判定枠の余剰 +370/週 に収まる。

PR1 の対象確定:

| slug | 未判定 | 扱い |
|---|---|---|
| `matsukaze-takashi` | 1,017 | **除外**（home 起動セッション＝PJ でない） |
| `rl-anything` | 800 | **alias fold** で `evolve-anything` に畳む（除外しない） |
| `zundamon-explainer` | 714 | **tracked に追加**（ユーザー裁定） |
| `ai-office` | 271 | **tracked に追加**（ユーザー裁定） |
| その他13 slug | 140 | **除外**（`build`/`docs`/`js`/`chain`/… パス断片由来のゴミ slug） |

→ PR1 で実際に judge 対象から外れるのは **1,157件**（当初見積り 2,942件から縮小）。
週あたりの余剰は **+200 → +232 件**（実測。§2 PR1 の効能表に反映済み）。
`tracked_projects` への2 PJ 追加は `fleet-config.json` の更新なので PR1 に含める。

**この裁定により PR1 の効果は当初より小さくなった。** それでも実施する理由は §2 PR1 の
「PR1 の価値は、正直に書くと次の3点」に記載した（費用の無駄を止める / 届かない在庫を明示する /
除外を可視化する）。「在庫解消が速くなる」は**もはや主要な理由ではない**。

---

## 8. レビュー反映

### codex（`設計修正要`）

| 指摘 | 反映 |
|---|---|
| [Must]1 group に鮮度も単一 `signal_key` も無く順位キーが計算できない | §2 PR2-c の代表値契約表（`min(signal_keys)` / 既読差引き後の再計算） |
| [Must]2 「二重ソートは冪等」は誤り。`max_groups=3` の早期打ち切りで4件目以降が候補外 | §2 PR2-b で**順位と打ち切りの分離**に設計変更。主張を撤回 |
| [Must]3 `source_correction_keys` は enrich を通らず provenance が伝播しない | §3「E をやるとしたら」1 に保存（E2 凍結） |
| [Must]4 再提示抑止が逆（reject 後は同 ID 再 emit、accept 後は新 ID 再生成） | 同 2。**issue #446 として起票**（A レーン全体の性質） |
| [Must]5 E1 は承認 provenance を機械強制せず信頼境界が変わらない | §3 で頭が裁定（**実施するが [Must]5 の5要件を設計要件に取り込む**） |
| [Must]6 correction → pattern の schema が未定義 | §3「E をやるとしたら」3 に保存 |
| [Should]1 「どこにも入らない」は不正確。ADR ゲート変更は明示裁定せよ | §1.2(1) の表現を厳密化 + ADR-054 §5 Phase E で裁定 |
| [Should]2 tracked は絶対パス / utterance は slug。alias fold と処理順が未設計 | §2 PR1 の契約1・2 |
| [Should]3 除外内訳の返却 schema を全分岐で定義せよ | §2 PR1 の契約4 |
| [Should]4 PR3/PR5 の「書込境界衝突」は誤り | §4 で撤回 |
| [Should]5 E0 の合格判定が量だけで弱い | §3「E をやるとしたら」4 に保存 |
| [Should]6 E0 不通過時の扱いを設計者が決めよ | §3 で凍結に確定 |
| [Nit]1 `cross_pj_confirmed` の型・空値・順序・表示上限 | §2 PR2-c / PR2-d |
| [Nit]2 契約テストに同値・複数 signal_keys・代表PJ消失を追加 | §2 PR2-e |
| [Nit]3 `--accepted` と既存 `--accept` の名前衝突 | §3 の E1 設計要件5 |
| [Nit]4 shrink-freeze の blocking contract を検証計画に明示 | §5 |

### tacchi（`設計修正要` — 条件付き着手可）

| 指摘 | 反映 |
|---|---|
| [Must] 順位規則の前に **read 時 machinery 除外**が要る（朝の候補300件中47件が委譲メッセージ・上位10件中6件） | §2 **PR2-a を新設し最優先に**。頭が実測で裏取り済み（`is_machinery_prompt` が 47/47 捕捉） |
| [Must] 規則1が global レーンを持ち上げない（confirmed と global observed は別物） | §2 PR2-c のキー1を「confirmed **または** global 所属」に再定義 |
| [Must] E0 は「実施予定」でなく「実測済み・不通過」として §1 に取り込み、今この設計書で裁定せよ | §1.2(3) へ移動 + §3 で凍結を裁定 |
| [Should] `detected_at` は判定時刻であって発話時刻ではない（在庫消化で「4ヶ月前の発話がトップ」） | §2 PR2-c のキー3を**発話時刻（read 時 join）**に変更 |
| [Should] `cross_pj_confirmed` は今朝 発火0件＝実質 no-op。発話の実時刻と観測ベース cross-PJ を出せ | §2 PR2-d を全面改訂 |
| [Should] 「52週→20週」は三重の但し書きが要る | §2 PR1 の効能を「余剰 +370/週」に書き換え |
| [Must] 「配線するだけで下流は改造不要」は形式的に真だが実質空（中身の流れないパイプ） | §3 で E2 凍結。当該表現を削除 |
| 観点3 §7（古い在庫）は設計で決められる。ドラフトが内部矛盾（52週→20週を売りつつ腐らせる） | §2 PR1 の契約5（cutoff 宣言 + 件数 surface） |
| 観点6 **柱3(b) は E2 なしで作れる**（skill_evolve レーンが生きている）。「E が前提」は過大主張 | §3 で「当面測らない」を撤回。#401 の記述も訂正 |
| 追加 corrections ストアの入力衛生（issue #445）/ 実ストア dry-run テスト / A1保留との相互作用 | §4「起票済み issue」・§2 PR2-e |
| 指摘「発見と設計が接続していない」（B-c を発見しながら識別力ゼロを確認せず第1キーに据えた） | §1.1 に順位キーの識別力の実測を追加し、PR2-c の設計をそれに合わせた |

### codex 2巡目（差分レビュー・`設計修正要` → 全て反映）

| 指摘 | 反映 |
|---|---|
| [Must]1 PR2-a の除外位置が誤り。`_read_new` だけでは `bootstrap_backlog._read_backlog` に machinery が残り母集団が分裂。除外件数の surface 経路も無い | §2 PR2-a を全面改訂（**`filter_actionable` に集約** + `_read_backlog` も同述語 + `excluded_machinery_*` の surface 契約） |
| [Must]2 「順位付けと limit を分離した呼び出し口」は既存 API に存在しない。巨大値では全件契約にならない | §2 PR2-b に **signature を明記**（`max_groups: Optional[int] = 5`・`None` で無制限・既定は5のまま） |
| [Must]3 「既読差引き後に代表値を再計算」は現データ形で実装不能（`_slim_group` が個別 key の情報を失う） | §2 PR2-c に **`signal_meta_by_key` の保持契約**を追加 |
| [Must]4 「非対話経路から accepted を渡せないことを機械的に保証」は実現不能（CLI は呼出元を認証できない） | §3 E1 要件2 を**「既知の call site をテストで固定」に弱め**、承認 token は別設計と明記。過剰約束を撤回 |
| [Should]1 join は可能だが PJ ごと・group ごとの query は全 DB 走査の反復 | §2 PR2-c に **O(U+S) の一括 map 方式**と失敗4種の区別を明記 |
| [Should]2 cutoff は TTL と重複しない（別段階・別時計）。ただし日付・境界・設定場所・返却分岐が未定義 | §2 PR1 契約5 に**仕様表**を追加（90日 / 境界規則 / userConfig / 全分岐返却）+ **正直な効果の見積り**（現在の在庫はほとんど減らない） |
| [Should]3 skill_evolve レーンの主張はコード上正しいが「必ず積み始める」は保証できない | §3 の表現を「積み始めうる」に弱め、**PR3 後に実測**する条件を追加 |
| [Nit] キー4 の fixture 定義が不十分 | §2 PR2-e にキー1〜3 同値 + 既読差引きで順序が変わるケースを追加 |

**レビューは 2巡で打ち切る**（rules/design-review-gate: レビュアーの指摘が全て具体的な修正指示の形で
解釈の余地がないため、3巡目は手続きのための手続きになる）。
