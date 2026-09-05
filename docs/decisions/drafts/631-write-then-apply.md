# 設計: 朝の「ルールに書く」を書く工程と `--apply` 記録まで一続きにする（#631）

- 起票: #631／関連: #587 #622 #467 #632
- 状態: 設計案（暫定含む・レビュー未通過）

## 1. 完成条件

① **守る対象**: 朝の y/n で「ルールに書く」を選んだ指摘が、実際にルールへ反映され、かつその反映が
`reflect_apply_events.jsonl` に記録されること（記録がなければ柱2は測定できない＝#587）。
② **信頼境界**: 脅威ではなく運用ミスを対象とする。手順を実行するのは常に Claude（agent）と人間の
y/n で、悪意ある入力は数えない。
③ **対象外**: `--apply` 自体の実装（既存・`skills/reflect/scripts/reflect.py:1044-1470` で完成済み）。
`target_kind` の集計対象拡張（memory/refs を柱2に数えるか）は #632 が別途裁定するので、本設計は
「#632 でどちらに決まっても壊れない」形にするだけで、値自体は決めない。
④ **blocking の定義**: 「`promoted` のまま `--apply` 記録が一切ない件が、同じ内容のまま2回目の朝提示に
達すること」をこの設計が防げているか。防げない場合は可視化で代替する。
⑤ **検証方法**: 下記「7. 検証」の陽性・陽性対照・陰性試験。
⑥ **目的文の物差しで削る量**: 目的文は CLAUDE.md「朝の30秒」「週1の数字」。
直接観測値: 30日で「書く」38回・反映記録1件（#631 本文、`bin/evolve-audit --growth` と
`corrections.jsonl` の status Counter 突合、取得日 2026-09-05T01:33Z）。
本設計が削れる量は**推定であり直接観測値ではないため 0 と書く**（新規メカニズムの効果は
運用してみないと測れない。事前に「往復が何%減る」と書かない）。

## 2. 前提の evidence

| # | 主張 | 値／コマンド | 取得日 |
|---|---|---|---|
| 1 | 在庫60（applied 2 / promoted 45 / pending 13）、30日提示77回（promoted 38 / already_reflected 26 / rejected 13）、反映記録1件 | #631 本文（`bin/evolve-audit --growth` と `corrections.jsonl` status Counter 突合） | 2026-09-05T01:33Z |
| 2 | 通知文言（`scripts/lib/daily/proposal_digest.py:667-679`）は既に「起草→追記→`--apply`」の3点セットを明記し、旧「promote-weak で止まる」文面ではない | `sed -n '640,679p' scripts/lib/daily/proposal_digest.py`（本設計作業中に直接確認） | 2026-09-05 |
| 3 | 反映先つき4択の手順書（`skills/evolve/references/correction-review.md:112-131`）も3点セット（昇格→反映先提案→Edit/Write→`--apply`）を既に規定 | 同ファイル読了 | 2026-09-05 |
| 4 | `--apply` は `--target-path`/`--draft-line-file` 必須、rules 配下は `--before-content-file` も必須（省略でエラー停止） | `skills/reflect/scripts/reflect.py:1044-1060,1312-1334` | 2026-09-05 |
| 5 | `correction_backlog.py` は `reflect_status=="promoted"` かつ非 invalidated を timestamp 昇順（古い順）で読み取り専用に返す。`timestamp` は既に `_format_backlog_item` の出力に含まれる | `scripts/lib/correction_semantic/correction_backlog.py:63-136` | 2026-09-05 |
| 6 | #541 D-2 により `already_reflected` は `--promote-weak` を呼ばず correction を作らないため、在庫レーンにも新規4択レーンにも出ない（`promoted` とは非対称） | `scripts/lib/daily/proposal_digest.py:661-666` | 2026-09-05 |

**3の evidence が意味すること**: 文書と通知は #541/#498（2026-08-30 マージ）時点で既に「一続き」の
手順を書いている。**#631 が実測した往復（38回書く→1件反映）は、文言不足ではなく、多段の手順を
毎回最後まで agent が実行し切れていないという実行時の落ち方**（手順は書いてあるが、選択後の
Edit→`--apply` まで agent が同一ターンで完了する保証がない）。この診断は次節「3. 現状の経路」で
file:line を続ける。

## 3. 現状の経路（`promoted` が終端になる箇所）

- `--promote-weak` は correction に `reflect_status="promoted"` を書き込むだけで完結する
  （`daily/proposal_digest.py:663-667` のコメントが明言）。この呼び出し**単体**は正しく完了する。
- 次の手順（反映先提案→Edit/Write→`--apply`）は**すべて agent の後続の自発的な行動**に依存する。
  これを強制する機構は無い（`correction-review.md:114-128` は agent 向けの手順記述であって、
  実行を保証するコードではない）。
- 未実行のまま次の朝を迎えると、`correction_backlog.py:106`（`reflect_status != "promoted"` で
  除外しない＝promoted は在庫として残り続ける）により**古い順で再提示される**。これが#631の
  「往復」の実体で、`promoted` は事実上の終端状態になっている。
- 再提示された在庫は現在「promoted」であること以外の情報を持たない。**何日前から`promoted`のまま
  かが見えない**ため、朝の30秒でユーザーが「これは前も見た指摘か」を判断する材料がない
  （`_format_backlog_item` は `timestamp` を返すが、呼び出し側の表示に経過日数への変換は無い —
  `daily/proposal_digest.py` 内に `correction_backlog` の出力を経過日数へ変換する処理は見当たらない）。

## 4. 提案

**選ばない案の説明も含め、機構数の比較を先に書く**（`think-before-coding.md` の「機構を足す前に
減らせないか」に従う）。

| 案 | やること | 機構数 | 選ばなかった場合の実害 |
|---|---|---|---|
| (A) 通知文言の書き換え | 「1を選んだ場合」をさらに強い MUST 文言にする | 0（既存文言の言い回し変更のみ） | 3節の evidence の通り、**既に3点セットを明記した文言で往復が起きている**ので、文言強化だけでは同じ実行時の落ち方（多段手順の未完走）を防げない。実害＝往復が続く |
| (B) `--promote-weak` に `--target-path` を必須化し1コマンド化 | 昇格コマンド自体に反映先を持たせ、`--promote-weak` の成功を「反映先が決定済み」の証拠にする | 1（CLI引数の追加・後方互換のため既定 None で任意運用に留めるなら実質0） | 選ばない場合、昇格と反映先決定が分離したままで、agentが反映先提案（手順1.5）を省略しても検出できない。ただし**Edit/Write と `--apply` はこの案でも別ターンに残る**ため、単独では#631の完成条件（往復ゼロ）を満たさない |
| (C) 在庫再提示に「promoted 済み・未反映 N日」を明示 | `correction_backlog` の出力（既存 `timestamp`）から経過日数を計算し、在庫3択の提示文言に1行足す。**新規ストア・新規フィールドなし**（read時導出、#379 凍結に抵触しない） | 1（表示ロジックの追加のみ、書込みなし） | 選ばない場合、ユーザーは「これは前回も書くと答えた指摘だ」と気づけないまま同じ判断を繰り返し、往復が可視化されないまま続く |

**推奨: (C) を単独で採用し、(A)(B) は見送る。**
理由: (C) は新規メカニズムを持たず（既存 `timestamp` の read-time 変換のみ）、`#379` 新設凍結・
`no-denylist-checks.md` のいずれにも抵触しない最小の1手で、#631 の完成条件のうち
「未反映として翌朝に区別されて出る」（issue本文の代替完了条件）を直接満たす。
(A) は3節の evidence により効果が実証的に否定されている（既に3点セットの文言があるのに往復が
起きた）ため追加しない。(B) は反映先決定の強制はできるが Edit/Write・`--apply` の未完走という
本体の問題を解決しないため、(C) だけでは物足りないと判断されるなら**次点候補**として issue 化する
（本設計では採用しない＝層を重ねない）。

### (C) の実装位置（設計のみ・実装しない）

- `scripts/lib/correction_semantic/correction_backlog.py` の `_format_backlog_item`
  （63-78行目）が返す `timestamp` を使い、**呼び出し側**（`daily/proposal_digest.py` の在庫3択
  組み立て箇所）で `now - timestamp` の日数を計算し、`{n}日前に「ルールに書く」を選択済み・
  未反映です` を在庫3択の設問文に追記する。**`correction_backlog.py` 自体は読み取り専用のまま
  変更しない**（責務は「在庫を返す」のままにし、表示文言の組み立ては呼び出し側に残す）。
- 経過日数のしきい値（何日から表示するか）は**暫定で0日（常に表示）**とする。表示を間引く基準は
  実データが無いと較正できないため、`measure-now-not-later.md` に従い暫定値のまま出し、
  必要ならユーザーの「うるさい」というフィードバックで調整する（新規ストアなしで調整可能 —
  表示ロジックの定数変更のみ）。

## 5. #632 の取り込み

本設計は #632 の裁定を**待たない**。理由: (C) は `reflect_target_kind` を一切参照しない
（`correction_backlog.py` は `reflect_status` と `timestamp` のみを見る。柱2集計の対象範囲を
決める `pillar2_metrics.py:233`／`reflect_apply_match.py:88-121`（`classify_reflect_target_kind`）
とは独立した経路）。#632 が「数える」「対象外と明記」のどちらに決まっても、本設計の在庫表示・
経過日数計算は変更不要。

## 6. 凍結との整合

- 新規ストア・新規 JSON キー・新規 weak_signal channel は作らない。(C) は既存 `corrections.jsonl`
  の `timestamp` フィールドを read 時に `now` と差分計算するだけで、永続化する値を1つも増やさない。
- `#379` 新設凍結の対象（新 store / observability section / advisory proposal adapter /
  weak_signal channel）のいずれにも該当しない。

## 7. 検証（設計時点の計画。実装フェーズで実施）

- **陽性**: `correction_backlog` に `timestamp` が3日前の `promoted` レコードを1件仕込み、
  在庫3択の提示文言に「3日前に...選択済み・未反映です」相当の文字列が現れることを確認する。
- **陽性対照**: `reflect_status="applied"` のレコード、または `timestamp` が当日のレコードでは
  経過日数の警告文言が出ない（`applied` は `correction_backlog` の母集団自体から除外済み
  ＝`correction_backlog.py:106` を根拠に対照とする）。
- **陰性試験（このチェックを通したまま壊す入力）**:
  1. `timestamp` が未来日時（時計ずれ・不正データ）のレコード → 負の日数にならず「0日」に丸める
     処理を要求する（丸めないと「-5日前」という表示になり得る）。
  2. `timestamp` がパース不能な文字列 → `correction_backlog.py:134` 既存の「末尾送り（epoch扱い）」
     と整合させ、経過日数表示は「不明」にフォールバックすることを要求する（例外を投げない）。

## 8. 未実測と実行契約

- **未実測**: (C) の表示追加により、実際に「往復（同じ指摘に何度も『書く』と答える）」が
  減るかどうかは運用してみないと分からない。ユーザーが経過日数を見ても行動を変えない可能性がある。
- **実行契約**:
  - 起点: 実装 PR マージ日
  - 再測条件: 実装後30日経過、または `promoted` レコードが新たに10件蓄積のいずれか早い方
  - 実行者: 頭（`bin/evolve-audit --growth` と `corrections.jsonl` status Counter 突合を再実行）
  - 判定者: ユーザー
  - 期限: 実装 PR マージから30日
  - 期限超過時: 「書く」選択の反復率（同一 idiom/representative が複数回『書く』と選ばれた比率）が
    実装前（#631 実測時点の分子分母は未算出。実装時に別途算出）から改善していなければ、(B) の
    追加 or 別設計をユーザーへ提起する

## ブロッカー

なし（本設計内で暫定案により完結。実装時の詳細な `daily/proposal_digest.py` 内の関数分割・
テストケース設計はレビュー後の実装フェーズで確定する）。
