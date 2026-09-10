# 設計: 朝の「ルールに書く」の直後に、反映に必要な識別子と次の手順を必ず出す（#631）

- 起票: #631／関連: #587 #622 #467 #632 #412 #637
- 状態: 設計案 v4（版ごとの訂正の履歴は issue #631 のコメントに残す）
- 範囲の裁定（ユーザー・2026-09-11）: 本設計が約束するのは「昇格の直後に、反映に必要な識別子と次の手順を
  出す」ところまで。ルールへの反映まで届くことは保証しない（届いたかは8節の観測量で見る）

## 1. 完成条件

① **守る対象**: `--promote-weak` が非 dry-run で昇格に成功した**全 key** について、`--apply` に渡す
`source_correction_id`（`corrections.jsonl` 上でちょうど1件に解決できるもの）と次の手順が、同じ実行の
stdout JSON に出ること。一意に解決できない key は識別子を出さず、その理由を出すこと。
② **信頼境界**: 運用ミスのみ（agent の多段手順の脱落・実装の取り違え）。悪意ある入力は数えない。
③ **対象外**:
  - ルールへの反映まで届くことの保証（上の範囲の裁定）
  - `--apply` の本体（`skills/reflect/scripts/reflect.py:1474-1635`）の変更
  - 既存コードの欠陥3件（実害0件・issue 候補。9節）
  - `reflect_target_kind` の集計対象（#632）
④ **blocking の定義（すべて機械判定）**:
  1. 非 dry-run で、`promoted_records` の key 集合が `promoted_keys` と一致する（昇格成功と失敗が混ざっても一致。
     失敗した key は `promoted_records` に出さない）
  2. `promoted_records` の各要素の `source_correction_id` が非 null なら、`resolve_source_correction_id`
     （`reflect.py:267`）で**ちょうど1件**に解決される。解決が1件でなければ `source_correction_id` は null、
     `reason` に理由が入る
  3. dry-run では `promoted_records` を出さない（書いていない correction の識別子を見せない）
  4. `--reject-weak` / `--already-reflected-weak` の stdout のキー集合が変わらない
  5. 永続化ゼロ: 非 dry-run の昇格後、`corrections.jsonl` に追記された行のキー集合が変更前と同じで、
     `weak_signals.jsonl` と `correction_review_seen.jsonl` の行にも新しいキーが無い
⑤ **検証方法**: 7節。
⑥ **目的文の物差しで削る量**: **0**（実装前。到達率への効果は運用でしか測れない＝8節）。
  0 のまま発注できるのは、系統独立レビューが必須ゲート（`independent-lineage`）のため。

## 2. 問題定義と evidence

**問題**: 「ルールに書く」を選んで昇格した指摘が、`--apply`（反映の記録）まで届かない。

| # | 主張 | 値／取得コマンド | 取得日 |
|---|---|---|---|
| 1 | 2026-08-06T01:33Z〜09-05T01:33Z に `correction_review_seen.jsonl` で `decision=promoted`（pj_slug=evolve-anything）となった key は **38（重複0）**、うち `correction_applied` に到達したのは **1** | `correction_review_seen.jsonl` を `pj_slug`・`decision`・`reviewed_at` で絞り `key` を数える／`reflect_apply_events.jsonl` の `correction_applied` を `confirms_attempt_id`→`target_correction_id` で引き、`corrections.jsonl` の `weak_signal_key` と突き合わせる | 2026-09-05 |
| 2 | 未到達37件がどの工程で止まったか | **計測中（7節の前に確定する）**。過去の会話記録から停止点を分類する | 2026-09-11 |
| 3 | 現在 `reflect_status=promoted` のまま残っている correction（evolve-anything）は **6件**。6件とも `session_id`/`timestamp` を持ち、識別子は計算できる | `corrections.jsonl` を `project_path` と `reflect_status` で絞る（codex 巡4 の読み取り集計・頭が 2026-09-11 07:19 JST に全PJ内訳 `promoted 196 / applied 28 / skipped 36 / pending 54` を別途確認） | 2026-09-11 |
| 4 | `--promote-weak` は昇格に成功した key だけを**即時**既読化する（`--apply` を待たない） | `skills/reflect/scripts/reflect.py:1377-1386` | 2026-09-11 |
| 5 | `--promote-weak` の stdout は `status` と `promote_signals` の戻り値・`confirmed_idioms`・`corrections_human_allpj` だけで、`--apply` に渡す識別子を出さない。agent は `make_source_correction_id(session_id, timestamp)`（`scripts/lib/memory_temporal.py:339`）で自分で組む必要がある | `skills/reflect/scripts/reflect.py:1401-1406` | 2026-09-11 |
| 6 | 昇格で作られる correction は `weak_signal_key` を持つ | `scripts/lib/correction_semantic/promote.py:392` | 2026-09-11 |
| 7 | 文書と実装の食い違い2点: (a) `correction-review.md:129`（手順6）は既読化を `--apply` の後に置くが、実装は `--promote-weak` の中で即時 (b) 同じ行は「`skipped` が空でなければ既読追記しない」とバッチ全体を未既読にする書き方だが、実装は成功 key だけを既読化する（`reflect.py:1377-1386`、テスト `skills/reflect/scripts/tests/test_reflect.py:2582-2620`）。どちらもコードが正 | `sed -n '112,131p' skills/evolve/references/correction-review.md` | 2026-09-11 |
| 8 | `--promote-weak` / `--reject-weak` / `--already-reflected-weak` の stdout のキー集合を固定するテストは無い（個別キーの存在確認だけ） | `grep -n 'promoted_weak' skills/reflect/scripts/tests/test_reflect.py`（1967/2001/2156/2676 行は個別キーの assert） | 2026-09-11 |

## 3. 提案

| 案 | やること | 機構数 | 採否 |
|---|---|---|---|
| (D) 昇格の直後に識別子と次の手順を出す | `reflect.py` の `--promote-weak` ハンドラ（非 dry-run）で、昇格後に `corrections.jsonl` を読み直し、`weak_signal_key` が `promoted_keys` に含まれ `reflect_status=promoted` の行から key ごとに要素を作って stdout の `promoted_records` に載せる。各要素は `{key, correction_id, source_correction_id, reason, next_step}`。`source_correction_id` は `make_source_correction_id` で作り、`resolve_source_correction_id` で1件に解決できたときだけ出す。`next_step` は `--apply <source_correction_id> --target-path <反映先> --draft-line-file <本文ファイル> [--before-content-file <反映前の全文>]` の形で、**<> は未確定の値**と明記する（そのまま実行できるコマンドとは呼ばない） | 2（stdout への追加1・案内文の書き換え1。新規ストア・新規フィールド・`promote_signals` の戻り値変更なし） | **採用** |
| (E) SessionStart でも在庫を出す | — | — | 今回は採らない。(D) の効果を8節で見てから |
| (D') 既読化を `--apply` 成功時へ移す | — | — | 今回は採らない。`#412 [Must]5`（`reflect.py:1377-1386` のコメント）の「昇格できた key だけを即座に既読化する」契約に触れる |

**組み立てを CLI 側に置く理由**: 昇格した correction は `weak_signal_key` を持つ（2節#6）ので、
`promote_signals`（`scripts/lib/correction_semantic/promote.py:475-590`）の戻り値を変えずに、CLI が
追記後のファイルから組み立てられる。戻り値を変えると、stdout ではない自動昇格
（`scripts/lib/correction_semantic/idiom_autopromote.py:162-180`）の内部結果にも新しいキーが生え、
#379 の裁定範囲（stdout の一時キー）を越える。

**案内文**: `scripts/lib/daily/proposal_digest.py:676` の「1 を選んだ場合」を「出力の `promoted_records` の
識別子を使い、全件 `--apply` まで進める」へ書き換える。識別子が null の key は「識別子を出せません（理由）。
`--view` で確認してください」と表示する。

**文書の訂正（本 PR に含める）**: `skills/evolve/references/correction-review.md` 手順4・6 と
`skills/evolve/SKILL.md:256` を実装に合わせる。(a) 既読化は `--promote-weak` の実行と同時に終わる
(b) 既読化されるのは昇格に成功した key だけで、失敗した key は未既読のまま残る
(c) `source_correction_id` は自分で組まず `promoted_records` の値を使う。

## 4. 凍結との整合（ユーザー裁定済み）

`promoted_records` は `--promote-weak` の stdout JSON の一時キーで、永続化ゼロ（④-5 で検査する）。
ユーザー裁定（2026-09-06）で #379 の凍結対象外。`promote_signals` の戻り値・永続ストア・evolve phases の
result キーには何も足さない。

## 5. #632 との関係

(D) は `--apply` の記録経路を変えないため、`reflect_target_kind` の集計対象（#632）と独立。

## 6. 取り消し後の数え方

8節の分子は「現在有効な反映」とする。取り消し（`--revoke`）を畳んだ状態は `reflect.py:143-170`
（`_active_applied`、`fold_corrections` は `scripts/lib/reflect_fold.py:141`）が正典。一度でも反映した件数
（取り消し前）は参考値として並記する。

## 7. 検証

- **陽性**: 非 dry-run で2 key を昇格し、`promoted_records` の key 集合が `promoted_keys` と一致し、
  各 `source_correction_id` が `resolve_source_correction_id` で1件に解決されること（④-1・2）
- **陽性対照**: `--reject-weak` / `--already-reflected-weak` の stdout キー集合を golden で固定し、
  変わらないこと（④-4。golden は本 PR で新設する。現状は無い＝2節#8）
- **陰性試験**（それぞれ、壊した実装で赤くなることを確かめる）:
  1. 昇格成功1件＋失敗1件（期限切れ）を同時に渡す → `promoted_records` は成功の1件だけ。失敗 key は
     表示されず既読にもならない（④-1）
  2. 同じ `session_id`・同じ時刻（`datetime.now` を固定）の2件を1回で昇格する → 識別子が衝突するので
     両方とも `source_correction_id` は null で `reason` が入る。衝突した識別子を出さない（④-2。
     実データ313件の衝突は0件＝codex 巡4 の集計）
  3. `--dry-run` → `promoted_records` が出ない（④-3）
  4. 永続化ゼロ: 非 dry-run 後に `corrections.jsonl` の新規行・`weak_signals.jsonl`・
     `correction_review_seen.jsonl` のキー集合を変更前と比べる。`promoted_records` を誤って
     correction に載せる実装で赤くなること（④-5）
- **既存テスト**: `promote_signals` の既存テストが無修正で通ること（戻り値を変えていない証拠）

## 8. 計測と実行契約

- **measure-now の3問**:
  ① 今日作れるか: 停止点の分類（2節#2）を今日の会話記録から作る（計測中・本設計の確定前に反映する）
  ② 片側だけでも今出る結論: 残っている6件は識別子を計算できる（2節#3）＝識別子が組めないことは6件の
  止まりの原因ではない可能性がある。これは2節#2の分類で確かめる
  ③ 既存データで代理できるか: 到達率そのものは、(D) の出力を見た agent がどう動くかに依存するため
  過去データでは代理できない。これだけは運用後に測る
- **実行契約**:
  - 起点: 実装 PR マージ日
  - 再測条件: マージ日以降に `decision=promoted` になった key が **10件**に達した時点
  - 観測量: そのうち現在有効な `correction_applied` に届いた件数（6節）／分母
  - 基準値: 1/38（2026-09-05T01:33Z 時点の30日窓で凍結）
  - 実行者: 頭／判定者: ユーザー
  - 期限: マージから30日（10件に届かなければ、その時点の件数で判定する）
  - 期限超過時: 基準値から改善していなければ (E) か (D') の再検討をユーザーへ提起する

## 9. issue 候補（本設計の対象外・実害0件。起票は合意後）

codex 巡4 で見つかった既存コードの欠陥。いずれも #631 の変更では触らず、新しい経路も増やさない。

- `--apply` で確認イベントの追記だけが失敗すると、status=applied のまま exit 0 になる
  （`reflect.py:1581-1603`）。実害0件（確認イベントの無い applied 4件はすべて専用ストア導入前の
  2026-07-01〜08-19）
- `_rewrite_promoted` が store_write を通らず直接置き換える（`promote.py:405-430`）。既存の経路
- `append_jsonl` が dry_run でも `open(..., "a")` で空ファイルを作る（`scripts/lib/rl_common/persistence.py:223`）。
  実データのファイルは既に在るため実害0件
- `scripts/lib/correction_semantic/correction_backlog.py:70` の `age_days` が未来日時で負値になる

## ブロッカー

なし。
