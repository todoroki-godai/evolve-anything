# 504: `prev_action` は判断材料にならない — 表示と説明可否判定から外す

status: draft（実装着手前レビュー用・**rev2**）
関連: #504 / #498（判断材料の配線）/ #501（中身のない rephrase の除外）/ ADR-054

## 0. round1（codex）対応表

| 指摘 | 対応 |
|------|------|
| [Must] E5（新規 rephrase が prev_action だけで説明可能になる）は producer 契約に反し成立しない | **撤回**。実コードで確認（`detectors.py:290-301` の rephrase provenance に `prev_action` は無い）。§2 を producer 契約の実測（E5'）に差し替え、将来増加の主張は「表示ノイズの発生率」（E7）へ置き換えた |
| [Must] §2 が内部矛盾 | E5 撤回により解消。提示件数は変更前後で不変（E4）と明記 |
| [Must]「ツール名列は判断材料として無価値」は未測定 | **主張を落とした**。根拠を「仕様（1行要約）と実装（ツール名連結）の乖離」という検証可能な事実に置き換え、価値の有無は §6 に未実測として明記（測定不能の理由も記載） |
| [Must] 陰性試験 #7 は変異でなく正常系 | 陽性対照・境界試験へ移動。陰性試験を別の変異で7件に組み直した |
| [Must] 変異 #3（文言改名）は I2 を通過する | 不変条件を文字列禁止でなく **sentinel の taint 不到達 + 出力のリスト等値**に作り直した（I2/I3） |
| [Must] 変異 #4/#5（別経路への退避）も通過する | 同上。`build_session_proposals` の JSON 全体・`systemMessage`・`build_proposal_prompt` の**全 user/Claude 可視 surface** を sentinel で end-to-end 検査する |
| [Should] 削除範囲は概ね正しい（reader 0 を確認） | 採用。§3.1 に reader の内訳を明記 |
| [Should] 文書更新が不足（`spec/components-feedback.md` / 生成物 `docs/site/reference.html`） | 採用。§3.1-6/7 に追加 |
| [Should] E1 は「現行 writer 由来なら tool_use.name 列」に狭めるべき | 採用。E1 を書き直した |
| [Should] I1 を taint-propagation 不変条件にすべき | 採用（I2 として独立させ、I1 は提示可否に限定） |
| [Nit] 変異 #1/#2 が同じ不変条件しか見ていない | 採用。故障領域を提示可否 / slim schema / 表示文 / 記録内容 / Claude 側チャネル / 検査自身の6領域に分散した |

## 1. 問題

#498 で朝の改善案提示に `evidence.prev_action` を配線した結果、実データの提示文がこうなる:

```
背景: 直前の作業: Bash,ToolSearch,Bash,SendMessage,Bash,Bash・1回検知
```

`prev_action` の仕様は「直前 AI 行動の**1行要約**」（`daily_review.py:401` docstring /
`representative.prev_action_summary` docstring「現状 prev_action は既に短い行（"Edit foo.py" 等）」）
だが、実装はツール名の連結である。**仕様と実装が食い違っている**のがこの issue の事実側の中身で、
「その連結が読み手の役に立つか」は本設計では主張しない（§6）。

害は表示だけではない。`proposal_digest._group_has_explanation`（#498 要件4）は
**`prev_action` または `reason` があれば「説明できる」**と判定する。`prev_action` が
ツール名列である以上、この分岐は「1行要約がある」という誤った前提の上に立っている。

## 2. 実測（すべて 2026-08-18・本番データ）

| # | 主張 | 実測値 | 取得方法 |
|---|------|--------|----------|
| E1 | 現行 writer が入れる `prev_action` は `tool_use.name` の連結である | 単一 writer。`extractor.py:366` が `_format_prev_action(pending_tool_names)`（同 235-241）を呼ぶ。`pending_tool_names` は assistant content の `type=="tool_use"` の `name` のみ（同 227-232）。`name` に形式 validation は無く、10 件超では末尾に `…` が付く | `grep -rn prev_action scripts/lib/utterance_archive/*.py` + 実コード読み |
| E2 | 現に格納されている非 null 値は 100% がツール名の連結形 | 全 19,182 行中 非 null 308 行。うち「カンマ区切りの各要素が識別子形式」= **308/308（100%）**、それ以外 **0 件** | utterances.db を `read_only=True` で走査・正規表現 `^[A-Za-z_][A-Za-z0-9_:.\-]*$` |
| E3 | 充填率は現行 extractor で約 64% | 版数別 非 null: v1 140/16,281 / v2 **0**/2,632（ADR-054 §5 の既知バグ）/ v3 62/103（60.2%）/ **v4 106/166（63.9%）**。v4 が現行版 | 同上・`GROUP BY extractor_version` |
| E4 | 説明可否判定から `prev_action` を外しても、**提示件数は1件も変わらない** | weak_signals 1,369 件。bare-utterance channel（llm_judge 411 / rephrase 190）の (prev_action, reason) 分布 = reason のみ **387** / どちらも無し **190** / 両方あり **24** / **prev_action のみ 0 件**。変更前後とも「提示 411 / 保留 190」 | weak_signals.jsonl 全件走査 |
| E5' | weak_signal の provenance に `prev_action` を入れる producer は **llm_judge だけ** | `batch.py:338-360` が `prov["prev_action"]` を書く。`rephrase`（`detectors.py:290-301`）・`esc_interrupt`・`manual_edit_after_ai`・`permission_deny`・`verbosity` の provenance には当該キーが無い | 実コード読み（round1 [Must] の指摘を検証） |
| E6 | llm_judge は `prev_action` と `reason` を**同じ verdict から同時に**書く | `batch.py:345-346`。したがって「prev_action のみ」は judge が reason 空文字を返した場合に限られ、実測 0/411 | 同上 + E4 |
| E7 | 表示ノイズの発生率は**上昇中**（これが今直す理由） | llm_judge シグナルのうち `prev_action` を持つ割合: 2026-06 **0/313（0%）** → 2026-08 **24/98（24.5%）**。llm_judge は utterances.db の行をそのまま読むため、上限は E3 の充填率 ≈64% | weak_signals.jsonl を `detected_at` 月別に集計 |
| E8 | 「ツール名列では文脈不足」は既に自分で記録済み | ADR-054（`054-four-pillars-completion-design.md:869`）の FN 分析に「文脈依存型（… `prev_action` の tool 名列では文脈不足）」。同 877-879 に「`prev_action` の有無が recall を変えるかは**未検証**」 | 既存文書 |

**E4 と E7 の関係**: 本変更は「どの提案を出すか」を1件も変えない（E4）。変えるのは
「出した提案に無意味な背景行が付くか」だけで、その発生率が 0% → 24.5% と上がっている（E7）。

## 3. 決定

issue #504 の3案のうち **案1（表示から外す）と案3（説明可否判定から外す）を同時採用**する。
案2（上流で本物の要約を作る）は**別 issue に分離**して不採用。

**「ツール名列かどうか」を判定する分類器は作らない。** E1/E2 より現行データでは偽側が
到達不能で、検証できない分岐になる。判定でなく配線の除去で実現する。

案2 を今やらない理由:
1. extractor 版数 bump + 既存 19,182 行の再 ingest が要る。ADR-054 §5-A1 は同種の全履歴
   migration を「judge は newest-first + 日次上限で古い行に到達しない」ため保留と裁定済み
2. assistant テキスト本文を保存内容に入れる設計判断（長さ上限・秘匿情報）が新規に必要
3. `prev_action` が判定精度に効くこと自体が未実証（E8）

## 3.1 変更

user 可視経路から `prev_action` を取り除く。utterances.db の列と、judge プロンプト
（`correction_semantic/prompt.py:111` / `batch.py:345`）は**触らない**（別系統・別目的）。

削除対象と、その production reader（round1 [Should] で確認済み・削除後は各 0 になる）:

| 削除するもの | 現在の production reader |
|--------------|--------------------------|
| 1. `daily/proposal_digest._group_has_explanation` の `prev_action` 分岐 | — |
| 2. `daily/proposal_digest._material_lines` の「直前の作業: 」 | — |
| 3. `daily/proposal_digest._slim_group` の `prev_action` キー（`_PREV_ACTION_TRUNC` 定数も） | 1 と 2 のみ |
| 4. `correction_semantic/daily_review._prev_action` と `evidence["prev_action"]` | 3 のみ |
| 5. `correction_semantic/representative.prev_action_summary` | 4 のみ |

6. docstring の実態合わせ: `daily_review.py:401` の `evidence` 契約記述、`representative.py:10-11`
   のモジュール docstring
7. `spec/components-feedback.md` の該当記述を更新する。生成物 `docs/site/reference.html` は
   本 PR では**更新しない**（`docs-refresh` が再生成する運用のため。CHANGELOG / `docs/archive/`
   は履歴なので改変しない）

## 3.2 変更しない

- utterances.db の `prev_action` 列と extractor（judge prompt が直接読む・案2 の土台）
- judge 側の `prompt.py` / `batch.py` の `prev_action` 利用（E8 のとおり効果は未検証だが別論点）
- `scripts/bench/a0_capture_replay.py`（bench 用 reader）
- `_EVIDENCE_TEXT_TRUNC` / `_REASON_TRUNC` と `reason` の扱い
- `count`（「N回検知」）— 背景行は `背景: N回検知` として残る

## 4. 不変条件（テストで固定する）

- **I1（提示可否）**: bare-utterance channel（llm_judge / rephrase）の group は、`reason` が
  空なら**必ず**保留され `excluded_context_missing_by_pj` に計上される。**同一入力に
  `provenance.prev_action` を足した版と足さない版で、`build_session_proposals` の出力が
  バイト等値である**（＝ prev_action は提示可否に一切影響しない）
- **I2（taint 不到達 / end-to-end）**: `provenance.prev_action` に sentinel 文字列を入れて
  digest を組んだとき、次の**すべて**に sentinel が 1 回も現れない。
  (a) `build_session_proposals` の返す構造全体（`json.dumps(..., ensure_ascii=False)` 全文）
  (b) `build_proposal_systemmessage` の出力（利用者可視）
  (c) `build_proposal_prompt` の出力（Claude 可視 = additionalContext）
- **I3（表示の等値）**: `count` のみを持つ group の `_material_lines` の戻り値が
  期待リストと**リスト等値**（包含でない）。かつ同じ group に prev_action 相当の入力を
  足しても戻り値が**変わらない**
- **I4（schema）**: `_slim_group` の返す dict に `prev_action` キーが**存在しない**
  （`assert "prev_action" not in slim`。値が空文字であることの検査では、下流が `.get()` で
  読む余地が残るため不可）

## 5. テスト方針

**陽性対照（緑のままであるべき・3件。陰性試験と混ぜて数えない）**
1. `reason` を持つ llm_judge group は従来どおり提示され、提示本文が変更前の golden と等値
2. `permission_deny` / `verbosity`（非 bare channel）は `reason` の有無に関係なく提示される
3. `count` のみの group の背景行が `  背景: 1回検知` になる

**境界試験（正常系・陰性試験に数えない・1件）**
- `reason` が空白のみ（半角 / 全角スペース / 改行 / タブ）の group は保留側に落ちる
  （`.strip()` 相当の正規化が効いているか）

**陰性試験（赤くなるべき・7件。実際に適用して赤を確認する）**

| # | 変異 | 壊す不変条件 | 通したい検査経路 |
|---|------|--------------|------------------|
| 1 | `_group_has_explanation` を常に True にする | I1 | 提示可否 |
| 2 | `_group_has_explanation` を `not reason` に反転する | I1 | 提示可否（反転） |
| 3 | `_slim_group` が `prev_action` キーを空文字で返す | I4 | slim schema |
| 4 | `_material_lines` が `直前の操作: ` という**別名**で prev_action を復活させる | I2(b) / I3 | 表示文（文言改名での回避） |
| 5 | `_recorded_message_preview` が prev_action を「記録される内容」に連結する | I2(a)(b) | 記録内容（別フィールドへの退避） |
| 6 | `build_proposal_prompt` 側にだけ prev_action を残す（利用者可視は綺麗なまま） | I2(c) | Claude 可視チャネル（片側だけの修正） |
| 7 | I2 の検査対象を `systemMessage` だけに縮める（検査自身の無効化） | — | 変異 6 が緑に戻ることで、検査の穴を証明する |

**実装者は上記に加えて、種類の違う変異を自分で2件以上考えて適用し結果を報告する**
（緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙する）。

## 6. 未実測の前提

- **「ツール名の連結が読み手の判断材料として無価値である」は測っていない**。測るには
  表示あり/なしで採否率・保留率を比較する必要があるが、提示は 1 セッション最大 2 件
  （`MAX_SESSION_PROPOSALS`）で、有意差を出せる n を貯めるには数ヶ月かかる。本設計は
  この主張に依拠せず、**仕様（1行要約）と実装（tool_use.name の連結）の乖離**（§1）と
  **提示件数が変わらないこと**（E4）だけを根拠にする
- **E7 の上限（≈64%）は外挿**。llm_judge が utterances.db の `prev_action` をそのまま
  転記する（`batch.py:345`）ことと E3 の充填率からの導出であり、その水準に達した観測は
  まだ無い（実測値は 24.5%）
- `prev_action` を judge prompt に渡すことが判定精度に効くかは**未検証**（ADR-054 が既に記録）。
  本変更はその経路を触らないため、この未検証は解消も悪化もしない
- 案2（本物の行動要約）が利用者の判断を実際に改善するかは未測定。分離 issue で扱う

## 7. スコープ外（別 issue）

- 案2: `prev_action` を実際の行動要約にする（extractor 拡張 + 再 ingest + 保存内容の設計）
- `scripts/lib/daily/proposal_digest.py` の分割（現在 796 行 / ハード上限 800）。本変更で
  数行減るが根本解決ではない。**振る舞い変更と純粋な移動を同一 PR に混ぜない**
  （`learning_audit_package_split`: keyset snapshot 不変で振る舞いを担保する分割手法）ため、
  本 PR とは別 PR で行う
