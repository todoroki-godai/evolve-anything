# 設計: 朝の「ルールに書く」を書く工程と `--apply` 記録まで一続きにする（#631）

- 起票: #631／関連: #587 #622 #467 #632 #412
- 状態: 設計案 v2（レビュー巡1で問題定義を訂正。案C撤回・案D採用）

## 0. レビュー巡1での訂正点（v1→v2）

- v1 は「未反映N日の表示」を新規案として提案したが、**既に実装済み**だった。
  `correction_backlog.py:70-74` の `_format_backlog_item` が `age_days` を返し、
  `skills/evolve/references/correction-review.md:173` の在庫3択の設問文が
  `「{message}」（{age_days}日前から在庫）` を実際に出している。→ **案C は取り下げ**。
- v1 は「同一指摘に何度も『書く』と答える往復」を問題として書いたが、実測では**そのような往復は
  0件**。問題は「`promote` した38件のうち37件が `--apply` まで到達していない」という**1方向の脱落**
  だった。→ 問題定義を書き換える（1節・2節）。
- v1 は「在庫3択（`correction_backlog`）がSessionStart通知に出る」ことを前提にしていたが、
  実際は**SessionStart経路（`daily/proposal_digest.py`）は `correction_backlog` を一切参照しない**。
  在庫3択が出るのは `/evolve-anything:evolve` スキル実行時（`skills/evolve/SKILL.md:257`）のみ。
  → 経路図を3節で書き直す。

## 1. 完成条件

① **守る対象**: 朝（または `/evolve` 実行時）の y/n で「ルールに書く」を選んだ指摘が、実際に
ルールへ反映され、`reflect_apply_events.jsonl` に `correction_applied` として記録されること。
② **信頼境界**: 運用ミスのみを対象とする（agentの多段手順の脱落・悪意ある入力は数えない）。
③ **対象外**: `--apply` 自体の実装（`skills/reflect/scripts/reflect.py:1044-1470` で完成済み）。
`reflect_target_kind` の集計対象拡張は #632 が別途裁定するため、本設計はそれに依存しない形にする。
④ **blocking の定義**: 「`decision=promoted` の件が、同セッション内で `--apply` 記録
（`reflect_apply_events.jsonl` の `correction_applied`）に達しない経路が残ること」。
⑤ **検証方法**: 7節の陽性・陽性対照・陰性試験。
⑥ **目的文の物差しで削る量**: 実装前のため **0**。基準値は下記2節の 1/38（3.5%）。
本設計の効果（この比率がどこまで上がるか）は運用実績でしか測れないため見積もりを書かない。

## 2. 問題定義（訂正後）と evidence

**訂正後の問題定義**: 「同一指摘への往復」ではなく「`promote` 後に `--apply` 記録へ到達しない」
という**片道の脱落**が主因。

| # | 主張 | 値／取得コマンド | 取得日 |
|---|---|---|---|
| 1 | `corrections.jsonl`（project_path に `evolve-anything` を含む行）の `reflect_status` 内訳: `promoted 2 / applied 20 / skipped 25 / pending 14`。**v1 に書いた「promoted 45」は仕分け前スナップショット**（#631 起票時点、2026-09-05T01:33Z）で、現在値とは別物 | `python3 -c` で `corrections.jsonl` を読み `project_path` に `evolve-anything` を含む行の `reflect_status` を `Counter` 集計 | 2026-09-05（本設計作業時点） |
| 2 | `correction_review_seen.jsonl` を `pj_slug=="evolve-anything"`・`decision=="promoted"`・直近30日で絞ると **38行・distinct key 38・重複0**（同じ key が2回以上 `promoted` と記録された行は無い） | `python3 -c`（`~/.claude/evolve-anything/correction_review_seen.jsonl` を読み `key` を `Counter`。ウィンドウは `reviewed_at` 直近30日） | 2026-09-05 |
| 3 | `reflect.py:1218` は `--promote-weak` ハンドラ内で `record_reviewed(res["promoted_keys"], ..., decision="promoted", ...)` を**即時**呼ぶ（`--apply` を待たない）。この既読化により同一 key は二度と朝の新規4択に出ない（`daily/proposal_digest.py:667-679` のコメントが根拠） | `sed -n '1174,1237p' skills/reflect/scripts/reflect.py` | 2026-09-05 |
| 4 | `daily/proposal_digest.py`（SessionStart 経路の `build_proposal_digest`・339行目／`build_session_proposals`・456行目）は `correction_backlog` を**呼んでいない**（grep で一致0件）。在庫3択が出るのは `/evolve-anything:evolve` スキル実行時のみ（`skills/evolve/SKILL.md:257`「`daily.correction_backlog` が非空のとき...在庫3択で確認する（MUST）」） | `grep -n "correction_backlog" scripts/lib/daily/proposal_digest.py`（0件）／`skills/evolve/SKILL.md:256-257` | 2026-09-05 |
| 5 | `--promote-weak` の stdout JSON（`reflect.py:1231-1237`）は `status/promoted_keys/skipped/confirmed_idioms/corrections_human_allpj` のみを返す。次に打つべき `--apply <source_correction_id> --target-path ... --draft-line-file ...` の**雛形は印字されない**。agent が `make_source_correction_id(session_id, timestamp)`（`memory_temporal.py:339`）を自力で組む必要がある | 同上コード読了 | 2026-09-05 |
| 6 | 文書とコードの食い違い: `correction-review.md:129`「6. 既読化: ...を `record_reviewed(...)` に渡す」は手順4（`--apply`）の**後**の手順6として書かれ、`skills/evolve/SKILL.md:256` も「①ルールに書く」→...→ `--apply` で実在確認してから」と読める語順だが、実装（上記#3）は `--promote-weak` の中で `--apply` を待たずに既読化する。**既読化のタイミングは文書の読後感と逆**（コードが正） | `sed -n '112,131p' skills/evolve/references/correction-review.md` と #3 の対比 | 2026-09-05 |

**この5件から導かれる脱落点**: 「promote」を選んだ時点で（a）既読化は完了し二度と聞かれない、
（b）次にすべき `--apply` コマンドの組み立て材料（`source_correction_id`）は agent 側の自力計算に
委ねられている。（a）と（b）が重なると、agent がその場で `--apply` まで実行しない限り、
**再提示という形での「気づき直しの機会」自体が失われる**（38件中37件で実際に起きた）。

## 3. 現状の経路（訂正後）

```
SessionStart（毎回）
  └─ daily/proposal_digest.py: build_proposal_digest / build_session_proposals
       └─ 新規4択（反映先つき）… correction_backlog は不参照
            └─「1 ルールに書く」選択
                 └─ --promote-weak 実行
                      ├─ corrections.jsonl の reflect_status を "promoted" へ（即時）
                      └─ record_reviewed(decision="promoted") で既読化（即時・#3）
                           → 以後 SessionStart の新規4択には二度と出ない
                 └─（ここから先の Edit/Write→--apply は agent の自発行動に依存。
                    stdout に次コマンドの雛形は出ない＝#5）
                 └─ 実行されなければ reflect_status="promoted" のまま放置される

/evolve-anything:evolve 実行時（ユーザーが明示的に叩いたときだけ）
  └─ 在庫3択（correction_backlog、#514）… reflect_status=="promoted" を古い順に提示
       └─「もう出さない」以外を選ばない限り、再度「1」を選んでも同じ脱落を繰り返しうる
```

**「promoted が終端になる」の実体**: 新規4択の再提示は既読化によって構造的に止まる
（正しい設計・二重質問防止）一方、その先の `--apply` への導線が SessionStart 経路には無く、
`/evolve` を明示的に叩かない限り可視化すらされない。

## 4. 提案

| 案 | やること | 機構数 | 採否 |
|---|---|---|---|
| (C) 未反映N日の表示 | — | — | **撤回**（0節のとおり実装済み・かつ在庫3択はSessionStartに出ないため、この案単独では脱落を減らせない） |
| (D) `--promote-weak` の出力に `--apply` 雛形コマンドをそのまま印字＋通知文言に「同セッション内で実行するまでが1手順」と明記 | ①`reflect.py` の `--promote-weak` ハンドラが `promoted_keys` それぞれの `source_correction_id` を計算し `apply_command_template` として stdout JSON に追加 ②`daily/proposal_digest.py:676` の「1 を選んだ場合」文言に「出力された `--apply` 雛形を同セッション内で実行するまでが1手順」を追記 | 2（CLIの出力追加1・文言追記1。いずれも既存コードへの追記で新規ストア無し） | **採用（推奨）** |
| (E) 朝の SessionStart 経路にも在庫（`correction_backlog`）を運ぶ | `build_proposal_digest`/`build_session_proposals` に `correction_backlog` の呼び出しを追加し、SessionStart でも在庫3択を出す | 1〜（`build_session_proposals` は #443 ADR-054 PR2-b で「順位と打ち切りを分離」する複雑な合成ロジックを持つため、在庫を混ぜると打ち切り件数・`max_groups` 配分の再設計が要る） | **今回は採らない**。理由: (D) は「promote した直後」に手を打つ設計で、agentがその場で `--apply` まで完走すれば在庫レーンに落ちる前に解決する。(E) は「一度promoteされ、放置され、翌朝以降に発見する」ための保険であり、(D) が効けば必要性が下がる。(D) 導入後も脱落が残るなら次点として issue 化する |
| (D') 既読化（`record_reviewed`）を `--promote-weak` 内から `--apply` 成功時へ移す | `reflect.py:1218` の呼び出し位置を変更 | 1（既存呼び出しの移動） | **今回は採らない**。理由: `reflect.py:1174-1218` のコメントが明示する `#412 [Must]5` の契約（「昇格できた key だけを即座に既読化しないと、TTL失効等で `promoted=0` でも既読化され二度と出ない silent failure が再発する」）に触れる。既読化を `--apply` 成功時に遅らせると、`--apply` を実行しないまま日をまたいだ場合に**同一指摘が新規4択へ再出現**するようになり、それは0節で確認した「往復は実際には起きていない」という現状の良い性質（二重質問防止）を壊す副作用がある。#412 の契約を壊さずに達成する保証が無いため、この設計では採用しない |

**推奨: (D)。**
理由: (D) は新規ストアを作らず、脱落点である「`--apply` に必要な `source_correction_id` を
agentが自力で組む」という手間そのものを消す。文言追記も既存の3点セット手順を「同セッション内で」
という時間的制約に強化するだけで、#412 の契約にも #379 凍結にも触れない最小の1手。

## 5. #632 の取り込み

本設計は #632 の裁定を待たない。(D) は `reflect_apply_events.jsonl` への記録経路
（`--apply` コマンド自体）を変更せず、出力の**手前**（`--promote-weak` の stdout）に雛形を足すだけ
なので、`reflect_target_kind` の集計対象（#632 の論点）とは独立。

## 6. 凍結との整合

- `apply_command_template` は `--promote-weak` の**stdout JSON への追加キー**であり、evolve phases
  の永続化された result キー・新規ストア・新規 weak_signal channel のいずれでもない。
  **頭の裁定（本設計に明記）**: CLI の一時的な標準出力キー追加は `#379` 新設凍結の対象外とする
  （凍結対象は「新設される store / observability section / advisory proposal adapter /
  weak_signal channel」であり、`--promote-weak` の呼び出し1回限りの stdout はどれにも該当しない）。
  永続化は一切増えない（`corrections.jsonl` 側にフィールドを足さない）。

## 7. 検証（設計時点の計画。実装フェーズで実施）

- **陽性**: `--promote-weak` を1件実行し、stdout JSON に `apply_command_template`
  （`--apply <source_correction_id> --target-path ... --draft-line-file ...` の文字列）が
  含まれることを確認する。
- **陽性対照**: 「いいえ」（`--reject-weak`）・「既に反映済み」（`--already-reflected-weak`）の
  stdout JSON には `apply_command_template` が含まれない（この2経路は反映を前提としないため）
  ことを確認し、経路が変わっていないことを示す。
- **陰性試験（このチェックを通したまま壊す入力）**:
  1. `promoted_keys` が空（全 key が昇格失敗）のとき、`apply_command_template` は空リストまたは
     キー自体を省略し、存在しない `source_correction_id` を捏造しないことを要求する。
  2. `session_id`/`timestamp` が欠落したレコードを昇格しようとしたとき、
     `make_source_correction_id` が例外を投げずに済む場合のみ雛形を出し、組めない場合は
     「雛形を生成できません・手動で `--view` から source_correction_id を確認してください」と
     明示することを要求する（黙って壊れた雛形を出さない）。

## 8. 未実測と実行契約

- **未実測**: (D) の雛形提示・文言強化により、agentが実際に同セッション内で `--apply` まで
  完走する率が上がるかは運用しないと分からない。「手間を減らせば実行される」という因果は仮説。
- **実行契約**:
  - 起点: 実装 PR マージ日
  - **再測条件（観測可能量）**: 直近30日の `decision=promoted` 件数に対する、同一 key 由来の
    `reflect_apply_events.jsonl` の `correction_applied` 到達件数の比率。
    **現在値（基準値）: 1/38 ≈ 2.6%**（2節#2の distinct key 38・うち `--apply` 記録に到達したのは
    #631 起票時点の実測で1件。算出コマンドは2節#2に同じ、`reflect_apply_events.jsonl` との
    correction_id 突合を追加する）
  - 実行者: 頭
  - 判定者: ユーザー
  - 期限: 実装 PR マージから30日
  - 期限超過時: 比率が基準値（1/38）から改善していなければ、(E) または (D') 代替案の再検討を
    ユーザーへ提起する

## Nit（issue候補・本設計の対象外）

- `correction_backlog.py:70` の `age_days = (datetime.now(timezone.utc) - ts).days` は
  `ts` が未来日時（時計ずれ等）のとき負値を返し得る。丸め処理が無い。別 issue 候補。

## ブロッカー

なし。
