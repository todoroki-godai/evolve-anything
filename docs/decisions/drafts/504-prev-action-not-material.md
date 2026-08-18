# 504: `prev_action` は判断材料にならない — 表示と説明可否判定から外す

status: draft（実装着手前レビュー用・**rev3**）
関連: #504 / #498（判断材料の配線）/ #501（中身のない rephrase の除外）/ ADR-054

## 0'. round2（codex）対応表

| 指摘 | 対応 |
|------|------|
| [Must] I2 が最終 surface（`restore_state.handle_session_start` の hook JSON）を漏らしている。collector/merge 層で再注入できる | 採用。I2 に (d) 最終 hook JSON 全文を追加 |
| [Must] 変異7（検査自身の縮小）は mutation kill として成立しない | 採用。collector/merge 層から最終 hook 出力へ再注入する **production 変異**に差し替え |
| [Must] E7 の「上限 ≈64%」は誤り（llm_judge 対象が偏れば 64% を超えうる） | 採用。上限の主張を**削除**。実測の推移（0% → 24.5%）だけを述べる |
| [Must] E4 は raw record 分布であり実提示件数ではない | 採用。「**説明可否ゲート上**の 411/190」と明記し、PJ scope / TTL / machinery 除外 / group 化 / 順位上限を通した実提示件数は**未測定**と §6 に記載 |
| [Must]「有意差に必要な標本は数ヶ月」も未算定 | 採用。**期間不明（必要標本数を未算定）**に修正 |
| [Should] 変異1と2は同じ I1・同じケースの重複 | 採用。変異2を `excluded_context_missing_by_pj` の計上漏れ（別の故障領域）へ差し替え |
| [Should] 変異4/5 の「通したい検査経路」が実コードと不整合（`_material_lines`/`_recorded_message_preview` を呼ぶのは `build_proposal_prompt` だけ） | 採用。§1 の問題記述も含めて訂正（下記 §1 注記） |
| [Should] E6 の「同じ verdict から」は不正確 | 採用。`reason` は verdict 由来 / `prev_action` は utterance 由来で、同じ provenance に同時に書いているだけ、と訂正。`reason` は空白のみもありうる点も追記 |
| [Nit] §3.1 の削除対象5件は過不足なし。ranking / global merge に KeyError も挙動変化も無い | 確認として記録（§3.1 に追記） |
| [Nit] E2/E3/E4/E7 の生数値は再実測で一致 | 記録のみ |

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

**この行が出る場所**（round2 [Should] 訂正）: 上の背景行を組み立てる `_material_lines` /
`_recorded_message_preview` を呼ぶのは `build_proposal_prompt`（Claude 可視 = additionalContext）
**だけ**で、`build_proposal_systemmessage`（利用者可視バナー）は呼ばない。つまりツール名列は
**Claude が読む提案文に入り、Claude がそれを y/n 提示に読み上げることで利用者に届く**。
利用者可視バナーに直接は出ない。

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
| E4 | 説明可否判定から `prev_action` を外しても、**ゲートの通過/保留は1件も変わらない** | weak_signals 1,369 件。bare-utterance channel（llm_judge 411 / rephrase 190）の (prev_action, reason) 分布 = reason のみ **387** / どちらも無し **190** / 両方あり **24** / **prev_action のみ 0 件**。「prev_action のみ」が 0 件である以上、`_group_has_explanation` の判定結果は変更前後で全件一致する。**これは説明可否ゲート上の件数であり、PJ scope / TTL / machinery 除外 / group 化 / 順位上限を通った実提示件数ではない**（§6） | weak_signals.jsonl 全件走査 |
| E5' | weak_signal の provenance に `prev_action` を入れる producer は **llm_judge だけ** | `batch.py:338-360` が `prov["prev_action"]` を書く。`rephrase`（`detectors.py:290-301`）・`esc_interrupt`・`manual_edit_after_ai`・`permission_deny`・`verbosity` の provenance には当該キーが無い | 実コード読み（round1 [Must] の指摘を検証） |
| E6 | llm_judge は `prev_action` と `reason` を**同じ provenance に同時に**書く | `batch.py:345-346`。ただし出所は別で、`reason` は verdict 由来・`prev_action` は対応する utterance 由来。parser は reason の欠落/null を空文字へ正規化し**空白のみの値も通す**ため、「prev_action のみ」は reason が空文字の場合に限らず空白のみの場合も含む。実測は 0/411 | 実コード読み + E4 |
| E7 | 表示ノイズの発生率は**上昇中**（これが今直す理由） | llm_judge シグナルのうち `prev_action` を持つ割合: 2026-06 **0/313（0%）** → 2026-08 **24/98（24.5%）**。llm_judge は utterances.db の行をそのまま読む（`batch.py:345`）ため、v4 行が増えるほど上がる。**到達水準は予測しない** — judge の対象選択が `prev_action` の有無と相関しうるため、E3 の平均充填率を上限とは言えない（round2 [Must]） | weak_signals.jsonl を `detected_at` 月別に集計 |
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

**下流への影響なし**（round2 [Nit] で確認）: `_slim_group` の戻り値を読む ranking は
`signal_keys` / `signal_meta_by_key` / `origin_pjs` 等のみを読み、global merge は group を
コピーし meta を union するだけ。`prev_action` キー削除で KeyError も順位変化も起きない。

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
  (b) `build_proposal_systemmessage` の出力（利用者可視バナー）
  (c) `build_proposal_prompt` の出力（Claude 可視 = additionalContext）
  (d) **`restore_state.handle_session_start` が返す hook JSON 全文**（`systemMessage` +
      `hookSpecificOutput.additionalContext` を含む最終 surface）。(a)〜(c) だけでは
      `collectors._build_session_proposal_output` / `NotificationItem.decision_text` /
      `_merge_notification_text` / `_build_additional_context` の各層で再注入できる
      （round2 [Must]）
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
| 2 | 保留した group を `excluded_context_missing_by_pj` に計上しない（保留自体はする） | I1 | 除外件数の surface（silence != evaluated が死ぬ） |
| 3 | `_slim_group` が `prev_action` キーを空文字で返す | I4 | slim schema |
| 4 | `_material_lines` が `直前の操作: ` という**別名**で prev_action を復活させる | I3 / I2(c)(d) | 表示文（文言改名での回避） |
| 5 | `_recorded_message_preview` が prev_action を「記録される内容」に連結する | I2(c)(d) | 記録内容（別フィールドへの退避） |
| 6 | `build_proposal_prompt` 側にだけ prev_action を残す（利用者可視バナーは綺麗なまま） | I2(c)(d) | Claude 可視チャネル（片側だけの修正） |
| 7 | `collectors._build_session_proposal_output` が `decision_text` に prev_action を連結する | I2(d) | hook 最終出力（digest より下流での再注入） |

**変異 4/5/6 の検査経路について**（round2 [Should]）: `_material_lines` と
`_recorded_message_preview` を呼ぶのは `build_proposal_prompt` だけなので、これらは
I2(a)/(b) では捕まらない。I2(c)/(d) と I3 で捕まえること。

**検査自身の無効化**（④類型）は上表とは別枠で1件実演する: I2 の検査対象を (b) だけに
縮めると変異 6/7 が緑に戻ることを示す。**これは mutation kill として数えない**（production
コードの変異ではないため・round2 [Must]）。

**実装者は上記に加えて、種類の違う変異を自分で2件以上考えて適用し結果を報告する**
（緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙する）。

## 6. 未実測の前提

- **「ツール名の連結が読み手の判断材料として無価値である」は測っていない**。測るには
  表示あり/なしで採否率・保留率を比較する必要がある。**必要標本数も所要期間も未算定**
  （round2 [Must]。提示は 1 セッション最大 2 件だが、検出したい効果量を決めていないため
  「数ヶ月」という見積り自体が根拠を持たない）。本設計はこの主張に依拠せず、
  **仕様（1行要約）と実装（tool_use.name の連結）の乖離**（§1）と
  **ゲートの通過/保留が変わらないこと**（E4）だけを根拠にする
- **実提示件数は未測定**。E4 は説明可否ゲート上の分布であり、PJ scope / TTL / machinery 除外 /
  group 化 / 順位上限（`MAX_SESSION_PROPOSALS`）を通った後に朝いくつ出るかは測っていない。
  ただし「prev_action のみ 0 件」から、本変更による差分が 0 であることは支持される
- **E7 が今後どの水準まで上がるかは予測しない**（round2 [Must]）。llm_judge の対象選択が
  `prev_action` の有無と相関しうるため、E3 の平均充填率（63.9%）を上限とは言えない。
  実測値は 2026-08 時点で 24.5%
- `prev_action` を judge prompt に渡すことが判定精度に効くかは**未検証**（ADR-054 が既に記録）。
  本変更はその経路を触らないため、この未検証は解消も悪化もしない
- 案2（本物の行動要約）が利用者の判断を実際に改善するかは未測定。分離 issue で扱う

## 7. スコープ外（別 issue）

- 案2: `prev_action` を実際の行動要約にする（extractor 拡張 + 再 ingest + 保存内容の設計）
- `scripts/lib/daily/proposal_digest.py` の分割（現在 796 行 / ハード上限 800）。本変更で
  数行減るが根本解決ではない。**振る舞い変更と純粋な移動を同一 PR に混ぜない**
  （`learning_audit_package_split`: keyset snapshot 不変で振る舞いを担保する分割手法）ため、
  本 PR とは別 PR で行う
