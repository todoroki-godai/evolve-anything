# #401: 週1回の戦果ボード要約を SessionStart に自動表示する

作成日: 2026-09-11 ／ 対象範囲: issue #401 のうち「週1回、ユーザーが何もしなくても戦果ボードの
要約（1〜3行）が目に入る導線」だけ（2026-09-11 ユーザー決定）。**設計のみ・実装コードは含まない**。

## 完成条件

① **守る対象**: 柱2「実際に反映された改善（直近30日）」の件数・柱3「指摘率」の最新の確定週の
値・柱4「戻せる採用」の件数の要約（1〜3行）が、ユーザーが何も操作しなくても週に1回は
SessionStart 通知に自然と現れること。

② **信頼境界**: 自分たちの実装ミス・性能劣化・既存契約（dry-run 純度・#379 新設凍結・
write barrier）の見落としのみを脅威とする。悪意ある第三者・攻撃者は数えない
（`live-checkout-guard.md` §① と同型の限定）。

③ **対象外**: 取り下げ候補（REGRESSED）の案内（issue 本文の指示どおり。現在0件 — 根拠は
下記「守る条件の件数」参照）／柱1（捕捉率）の表示／改善案への y/n 判断 UI／複数 PJ 横断表示／
push 型の非同期通知／表示先を SessionStart 以外に広げること。

④ **blocking の定義（機械判定できる条件だけ）**:
   - 同一 `pj_slug` × 同一 ISO 週で、要約の表示（＝ ack 相当の commit 呼び出し）が2回以上
     観測される
   - 新しい ISO 週に入り、かつ内部で `measured=True` のデータが存在するにもかかわらず、
     その週最初の SessionStart で要約（または「測定不能」の明示メッセージ）が一度も
     出ない（＝完全な沈黙）
   - どちらも `now` を注入可能な純粋関数として実装し、実カレンダーに依存せず単体テストで
     決定論的に再現する（③で述べた各 producer 関数はすべて `now:` 引数を既に持つ。
     `scripts/lib/correction_rate.py:678`／`scripts/lib/pillar2_metrics.py:168`
     いずれも確認済み）。

⑤ **検証方法**:
   - **陽性**: 週境界をまたいだ2回目の SessionStart 呼び出し（`now` を1週間進めて注入）で
     要約が再び出ることをテストする。
   - **陽性対照**: 同一 ISO 週内の2回目の SessionStart 呼び出し（`now` を1日だけ進めて注入）
     では要約が出ないことをテストする（意味を変えない書き換え＝同週内の追加呼び出しでも
     誤検出しない）。
   - **陰性試験（2種）**:
     1. 「ack 書き込みを削除する」変異（commit 呼び出しを no-op に差し替える）を当て、
        同週内2回目の呼び出しでも要約が出てしまう（=①の blocking 条件に触れる）ことを
        テストが検出できるか確認する。
     2. 「ISO週判定を1日前後にずらす」変異（`week_id_for` の代わりに固定文字列を返す
        フェイクへ差し替える）を当て、週境界をまたいでも要約が出ない（=②の blocking
        条件に触れる）ことをテストが検出できるか確認する。
   - 実装フェーズで上記2種の変異を実際に当てて緑のまま残らないことを確認するまで、
     この検査は「効いている」と扱わない（`verify-checks-by-breaking.md`）。

⑥ **目的の物差しで削る量**: **0**。本設計は「1周の壁時計を短くする」類の時間短縮ではなく、
「週1の数字を能動操作なしで見せる」という可視性・到達頻度の話であり、CLAUDE.md の目的文
（「効果は週1の数字で実感」）と同じ単位の直接観測値・算式を持たない。推定・代理指標
（例: 「気づきやすさが上がるはず」）は使わず、素直に 0 と書く。

## 守るべき条件の件数（母集団・取得日つき）

| # | 条件 | 母集団の定義 | 件数 | 取得日 | 扱い |
|---|---|---|---|---|---|
| 1 | 現状、柱2/3/4要約を週1回自動表示する機構 | `scripts/lib/session_notify/collectors.py` の `_build_*_output` 関数一覧（grep） | **0件**（該当関数なし） | 2026-09-11 | 対象（守る対象そのもの） |
| 2 | pillar2 計算がSessionStart hot-path 予算を超えている | `pillar2_metrics.count_applied_reflections` を3回計測した実測秒数（下記「所要時間の実測」） | **1件**（5.4〜8.3秒、`pitfall_hot_hook_eager_import` の数十ms 予算を大幅超過） | 2026-09-10 | 対象（設計上の主制約） |
| 3 | #379 新設凍結中の新規 store/section/adapter/channel | 本設計が追加提案する新規宣言の数（下記「凍結との関係」） | **0件**（既存 `evolve-queue.json`/`evolve-state.json` への新規キー追加のみ） | 2026-09-11（コード確認） | 落とす候補として明記（新設なしなので凍結条項は非該当） |
| 4 | 取り下げ候補（REGRESSED）件数 | `optimize_history_store.load_effective_history(slug)` の accepted のうち `verdict=="REGRESSED"` | **0件** | 2026-09-10T23:23:10Z | 落とす候補（③対象外の根拠。0件だが issue 本文の明示指示により対象外として残す＝0件でも落とさない類型②） |

## 設計上の問い1: 「週に1回だけ出す」をどう実現するか

### 選択肢比較

| 案 | 機構 | #379 凍結との関係 | 騒がしさ／リスク |
|---|---|---|---|
| (a) 既存 ack/marker への相乗り | `evolve-state.json`（`scripts/lib/store_registry.py:659-694` で宣言済み・`kind="json"`・`classification="workflow_state"`）に新規キー `weekly_board_last_shown` を追加。読み書きは既存の `skills/evolve/scripts/evolve/_state.py:127-144` の `load_evolve_state()`/`save_evolve_state()` と同型の関数を `session_notify` 側に実装。値は `{pj_slug: "<ISO週>"}` の小さい dict | **抵触しない**。`shrink_freeze.FROZEN_STORES` はストア basename の集合であり、既存ストア内の JSON キー追加は「新設」ではない（`scripts/lib/shrink_freeze.py:1-38` の docstring: 追加が止まるのは「新 store / observability section / advisory proposal adapter / weak_signal channel」の4種のみ） | 低い。既に `evolve-state.json` は複数 writer（`trigger_engine`／`evolve/_state.py`／`prune/skill_inspect.py`／`audit/orchestrator.py`）が個別キーを書く設計（`store_registry.py:662-672` の note）なので、5番目の writer を足すのは既存パターンの延長。**ただし** `save_evolve_state` はロックなし・全体上書き（`_state.py:138-144`）なので、他 writer と同時書込みが起きれば片方のキーが消える理論的競合がある（既存の pre-existing risk。#136 は per-PJ 値をこのファイルに置いたことで cross-PJ 汚染を起こした過去があり — `evolve/_state.py:105-124` の `_load_last_run` コメント参照 — 本設計は値をスカラーでなく `{pj_slug: ...}` の dict にすることで同型の汚染は回避する） |
| (b) 状態を持たない（曜日／ISO週から導出） | marker を持たず、`correction_rate.week_id_for(now)`（`scripts/lib/correction_rate.py:78`）が既存週と同じかを都度計算するだけで「今週まだ出していない」を判定しようとする方式 | 抵触しない（何も書かない） | **高い**。「状態を持たない」では「今週すでに出したか」を判定できない（1週間に何度もセッションを開けば毎回出る）。曜日固定（例: 月曜だけ出す）にしても、月曜に複数セッションを開けば同日中に何度も出る。ゼロ状態では原理的に「週1回」を満たせないため**不採用** |
| (c) daily runner（launchd 毎朝実行）への相乗り | `bin/evolve-daily-run` が毎朝 `evolve-queue.json` を上書き生成する際（`bin/evolve-daily-run:188-198` に `payload["llm_judge"]`/`payload["proposals"]` を足す precedent あり）、同様に `payload["weekly_board"]` を足す | 抵触しない（同じ precedent） | **中〜高**。daily runner はグローバル単発プロセスで、柱2（`count_applied_reflections`）は `project_root` 引数を要求する PJ スコープの計算（`scripts/lib/pillar2_metrics.py:168-175`, `scripts/lib/results_board.py:361-373`）。daily runner はどの対話セッションの PJ が「今週見るべき PJ」か知らない。全 tracked PJ 分を毎朝計算すると 5〜8秒 ×N PJ のバッチコストが積み増しになり、かつ「表示は週1回」の dedup 判定は結局セッション側（SessionStart）で別途持つ必要があるため、この案だけでは(a)の代替にならない（値の precompute 先としては候補になりうるが、dedup 機構としては(a)と併用が要る） |

### 推奨

**(a) を採用**（週1回の dedup 判定・ack マーカー）。(c) は値の precompute 先としては魅力的だが
PJ スコープの不一致（daily runner はグローバル・柱2は project_root 依存）を解消する追加機構が
要り、本 issue の範囲（表示導線だけ）を超える。(b) は状態ゼロでは要件を満たせないため不採用。

**選ばなかった場合に起きること**: (c) 単独を選ぶと「毎朝全 PJ 分を計算する」新しいバッチ責務が
増え、#379 の縮小方針（機構を増やさない）と逆行する。(b) を選ぶと「週1回」の約束を守れず、
CLAUDE.md の「効果は週1の数字で実感」という体験文と食い違う実装になる。

## 設計上の問い2: SessionStart の所要時間

### 所要時間の実測

対象: 実 DATA_DIR（`~/.claude/evolve-anything/`）・実 PJ（`/Users/matsukaze-takashi/matsukaze-utils/evolve-anything`）に対する読み取り専用呼び出し。3回連続実行、`time.perf_counter()` で計測。

```bash
export PYTHONPATH=/Users/matsukaze-takashi/wt/ea-401/scripts/lib
python3 -c "
import time
from pathlib import Path
from pillar2_metrics import count_applied_reflections
proj = Path('/Users/matsukaze-takashi/matsukaze-utils/evolve-anything')
for i in range(3):
    t0=time.perf_counter(); r=count_applied_reflections(proj); t1=time.perf_counter()
    print(t1-t0)
"
```

| 関数 | 用途 | 実測3回（秒） | 取得時刻（UTC） |
|---|---|---|---|
| `pillar2_metrics.count_applied_reflections(project_root)`（`scripts/lib/pillar2_metrics.py:168`） | 柱2 | 8.313 / 6.839 / 5.414 | 2026-09-10T23:19:58Z |
| `correction_rate.build_correction_rate_summary()`（`scripts/lib/correction_rate.py:678`） | 柱3 | 0.178 / 0.078 / 0.074 | 2026-09-10T23:20:03Z |
| `evolve_revert_listing.build_revert_listing()`（`scripts/lib/evolve_revert_listing.py:33`） | 柱4 | 0.016 / 0.015 / 0.011 | 2026-09-10T23:20:09Z |

柱2だけが `pitfall_hot_hook_eager_import.md`（本 PJ memory）が示す hook レイテンシ予算
（毎発火 hook はミリ秒〜数十ミリ秒オーダーであるべき — 同 pitfall の是正実測は
`114ms → 73ms`）を2桁以上超過している。柱3・柱4は同予算内に収まる。

### 代案（新 store は作れない前提で）

柱2の重さの根因は `count_applied_reflections` 内の `_read_stable_snapshot`
（`scripts/lib/pillar2_metrics.py:73-95`）が `corrections.jsonl`／`reflect_apply_events.jsonl`
を安定するまで最大3回読み直し、`fold_corrections` で全件突合していること（既存ストアの
全量スキャン。新 store を作って高速化する余地は #379 凍結で塞がれている）。

**採用する対策**: 柱2の呼び出しを「週に1回、まだ今週分を表示していない PJ の SessionStart」
だけに限定する（設問1の(a)のゲートと合成する）。この場合、重い呼び出しは
**PJ ごとに週1回だけ**発生し、hot-path（残り6日×N セッション）には一切乗らない。
1回だけなら 5〜8秒の SessionStart 遅延は許容範囲と判断する（暫定判断・下記「未確定」参照）。

軽量化（事前計算をどこかへ移す）は本 issue の範囲外の改善候補として issue 化を検討する
（daily runner 側で precompute する設問1(c)案の「PJ スコープ不一致」を解消する設計が前提になる
ため、単独の小改善では終わらない）。

## 設計上の問い3: 失敗時の見え方

既存の9系統の収集関数（`scripts/lib/session_notify/collectors.py`）はすべて
`try/except Exception` で包み、失敗時は stderr に1行出して `None` を返す（SessionStart 自体は
落とさない）契約を共有している。本機能もこの契約に従うが、「今週まだ表示していない」ケースで
計算が失敗した場合に**沈黙で握り潰さない**ため、以下を追加する:

- 計算失敗時も ISO週 marker は**更新する**（`live-checkout-guard.md` の fail-open の考え方＝
  「作業を止めないこと」だけに適用し、検出の健全性は fail-visible にする、という区別に倣う）。
  更新しないと壊れた状態が直るまで毎セッション5〜8秒の再試行が走り続ける（②の blocking
  条件「完全な沈黙」ではなく「毎回失敗の重い再試行」という別の劣化を生む）。
- 更新した上で、Tier1（`NotificationItem.tier=1`・budget に関わらず必ず出る区分。
  `scripts/lib/session_notify/merge.py:36-39` 参照）で「戦果ボード要約の生成に失敗しました
  （理由: …）」を1回だけ出す。既存の `icebox-verdicts.json`/`evolve-queue.json` 破損検出
  （`scripts/lib/session_notify/collectors.py:433-439`, `690-697`）と同じ「absent は沈黙・
  corrupt は Tier1 health notice」という2分法を踏襲する。

## 表示先・Tier

`scripts/lib/session_notify/collectors.py` に `_build_weekly_board_output()` を追加し、
`hooks/restore_state.py` の収集リストへ他9系統と並べて配線する（設計のみ。実装コードは
本 PR の対象外）。頻度が週1回と低いため **Tier1**（budget に関わらず必ず出る区分）を推奨する
— Tier2 にすると `TIER2_BUDGET_CHARS=400`（`scripts/lib/session_notify/merge.py:9`）の
奪い合いで他の通知に押し出され、「週1で見える」という約束が守れない回がありうる
（`merge.py:42-47` の overflow 挙動）。週1回しか発火しないため、Tier1 化による他通知への
圧迫は軽微と判断する（暫定判断）。

## 凍結との関係の判定と根拠

- 新規 store: **0件**。追加提案は既存の `evolve-queue.json`（宣言: `store_registry.py:791-808`。
  `kind="json"`／`classification="derived_cache"`／writer は daily runner）または
  `evolve-state.json`（宣言: `store_registry.py:659-694`。複数 writer 前提）への新規キー追加のみ。
  `shrink_freeze.FROZEN_STORES`（`scripts/lib/shrink_freeze.py:62-112`）はストア **basename** の
  集合であり、既存宣言済みストア内のキー追加はこの集合の要素を増やさない。
- 新規 observability section / advisory proposal adapter / weak_signal channel: **0件**。
  いずれも追加しない。
- 判定根拠 file:line: `scripts/lib/shrink_freeze.py:1-38`（凍結対象の定義＝4種のみ）、
  `scripts/lib/shrink_freeze.py:263-277`（`assert_no_new_keys` はキー**集合**の差分で判定し、
  対象は `FROZEN_STORES`/`FROZEN_OBSERVABILITY_SECTIONS`/`FROZEN_ADVISORY_PROPOSAL_ADAPTERS`/
  `FROZEN_WEAK_SIGNAL_CHANNELS` の4集合のみ）、`bin/evolve-daily-run:188-198`（既存ストアへの
  フィールド追加の precedent＝#409 `payload["proposals"]` 追加）。

## 未確定・暫定判断した点

1. **柱2計算（5〜8秒）を週1回だけとはいえ SessionStart 同期呼び出しにしてよいか** —
   暫定で「許容する」と判断したが、体感レイテンシへの影響はユーザー確認が必要（他の
   9系統は全て数十ms〜数百ms 級）。代案（daily runner 側での precompute）は PJ スコープ
   不一致の解消が前提で本 issue の範囲外とした。
2. **marker の保存場所は `evolve-state.json`（案a）と判断したが、`evolve-queue-state.jsonl`
   （PJ ごとの append-only jsonl・`store_registry.py` 内 `evolve-queue-state.jsonl` 宣言）を
   使う代替も同程度に妥当**。後者は「1ファイル1目的」の宣言慣習には忠実だが、既存の
   `last_evolve_at` という目的と無関係なキーを追加することになり、どちらが良いかはユーザー
   判断が必要（本設計は前者を推奨するが確定ではない）。
3. **Tier1 化の妥当性**（表示先・Tier節）は暫定。実装時に既存の Tier1 系統
   （trigger／evolve-drain／data-dir migration 等）との同時発火頻度を実測し、体感の
   圧迫が無いかを確認する必要がある。
4. **失敗時に marker を進める設計**（設問3）は「直るまで毎回失敗が繰り返される」ことを
   避けるための判断だが、結果として「その週は本当は直っていたのに表示されない」ケースを
   1週間分見逃す可能性がある。これは許容できるトレードオフと判断したが確定ではない。
