# #401: 週1回の戦果ボード要約を SessionStart に自動表示する（v3）

作成日: 2026-09-11（v1〜v3 いずれも同日。v1: SessionStart 側 ack marker 案 → v2: codex 巡1
「設計修正要」により daily runner 計算方式へ全面変更 → v3: codex 巡2「設計修正要」により
fail-visible の判定基準・破損時の扱い・Tier・文言を修正、ユーザー決定「縮めて実装へ」により
記述を圧縮）。対象範囲: issue #401 のうち「ユーザーが何もしなくても戦果ボードの要約
（1〜3行）が目に入る導線」だけ。**設計のみ・実装コードは含まない**。

## 完成条件

① **守る対象**: 柱2「実際に反映された改善（直近30日）」の件数・柱3「指摘率」の最新の確定週の
値・柱4「戻せる採用」の件数の要約（1〜3行）が、その ISO 週で daily runner が初めて成功した日
（`weekly_board.computed_on`）に、ユーザーが何も操作しなくても SessionStart に自然と現れること
（同じ日に何度セッションを開いても出続け、**日付が変わったら出ない**）。

② **信頼境界**: 自分たちの実装ミス・性能劣化・既存契約（dry-run 純度・#379 新設凍結・write
barrier）の見落としのみを脅威とする。悪意ある第三者は数えない（`live-checkout-guard.md` §①）。

③ **対象外**: 取り下げ候補（REGRESSED）の案内（現在0件・下記「守るべき条件の件数」）／柱1
（捕捉率）の表示／y/n 判断 UI／複数 PJ 横断表示（値は evolve-anything 本体固定）／push 型通知／
表示先を SessionStart 以外に広げること／SessionStart 側での集計・書込み／**手動実行と自動実行が
同日に重なった場合の表示ズレ（M3）**: `evolve-daily-run` が同日に複数回（例: launchd の定時実行
と手動デバッグ実行）走っても、鍵や排他制御は足さない。runner の入口に排他は無く、`evolve-queue.json`
は**ファイル全体が非 atomic に**書かれる（`bin/evolve-daily-run:88-100,178-199`・codex 巡2 M3/巡3）。
重なると、古い payload での上書きや JSON の破損が起こり得て、`weekly_board` だけでなく同じファイルを読む
queue・judge・proposal の通知にも影響する（この性質は本設計の前から在る）。本設計が足す3値の集計は
各ストアを**読むだけ**で書き換えないので、元データは失われない（runner の他工程＝ingest 等の書込みは
本設計の範囲外）。壊れた `weekly_board` は④の妥当性判定で翌日の再計算に回り、queue 全体の破損は既存の
破損分類（`corrupt`）で SessionStart に出る。現在の発生件数: **0件**
（2026-09-11・手動実行の運用実績なし）。

④ **blocking の定義（機械判定できる条件だけ）**:
   - **週の最初の実行で更新される**: 上書き前の `evolve-queue.json` から読める直前の
     `weekly_board` が**妥当**（dict で、`week_id` と `computed_on`（`YYYY-MM-DD`）がともに str、かつ
     `measured` が False でない）なら、その `week_id` を直前の週とする。妥当でなければ（欠落・型違い・
     `computed_on` 欠落・`measured: False` を含む）直前の週は `None` とする。直前の週が今回の ISO 週
     （`correction_rate.week_id_for(now)`, `scripts/lib/correction_rate.py:78`）と異なるときだけ
     3値を再計算し、`week_id`/`computed_on`（今日のローカル日付）を書き込む。**形の壊れ（M2a/b）
     は「異なる週」に自然に畳み込む**（妥当でなければ `None` になり `None != 今回の week_id` は常に真。
     `{"week_id": 今週}` のように `computed_on` だけ欠けた記録も再計算に回る）
   - **同週の2日目以降・同日2回目は不変**: 直前の `week_id` が今回と一致するなら再計算せず
     直前の `weekly_board` をそのまま維持する（`computed_on` も書き換えない）
   - **表示は `computed_on == 今日` の日だけ**: SessionStart は一致しなければ何も出さない
   - **黙って消えない（fail-visible）**: SessionStart は次のいずれかで Tier1 の1行 health
     「戦果ボードの要約を読めません（理由）」を出す — (a) `evolve-queue.json` が実在するのに
     `_resolve_queue_data()` の結果が dict でない（`[]`/`null`・import 失敗で `absent` になった場合を含む。
     実在の判定は `Path.exists()`）(b) `weekly_board` キーが在り dict でない、`measured: False`
     （理由は `reason` を出す）、または `week_id`/`computed_on` が str でない（`{"week_id": 今週}` のような
     欠落を含む）。**この health 判定は `computed_on == 今日` の非表示判定より先に行う**（日付比較を
     `.get()` で済ませて欠落を沈黙させない）(c) 新しい収集関数の中で例外が出た（既存の `try/except` で `None` を返さず、
     この health を返す）。ファイル自体が無いときは何も出さない（既存の absent と同じ）
   - **読取障害では `week_id` を進めない（M1）**: 3値のうち**いずれか1つでも読取障害
     （後述の各 `measured` フィールドが False）**なら `weekly_board` を
     `{"measured": False, "reason": ..., "generated_at": ...}` として書き、**`week_id` を書かない**
     （＝翌日また「異なる週」判定になり自己修復的に再試行する）。柱3の「確定週がまだ無い」
     （`point_week is None` だが読取自体は健全）は読取障害ではないため、`week_id` を進めて
     「データ蓄積中」を表示する
   - **3値は単一ソース関数のフィールドと一致する**（下表「3値の対応と読取障害の判定」）

キー欠落は沈黙する（旧 runner・未実行は形の壊れではない）。queue 全体の corrupt は既存 queue レーンだけが Tier1 を通知する。

⑤ **検証方法**:
   - **陽性**: 直前 `week_id` が先週のまま → 3値が更新され④の各関数の戻り値と一致する。
   - **陽性対照**: 直前 `week_id` が今回と同じ → 3値も `week_id` も変化しない。
   - **陰性試験（5種）**。measure-now 3問への回答: ①今日作れるか＝検査の入力（フィクスチャ）は
     既存テストの形で今日作れる（柱3は `scripts/lib/tests/test_correction_rate.py:654-680`、通知は
     `NotificationItem` の既存テスト）。変異そのものは実装が無いため今日は当てられない ②片側で今出る結論＝
     各変異で赤くなるべき検査の形（上の各項の入力と期待）は設計時点で確定している ③既存データで代用＝
     実データは使わず固定フィクスチャで足りる（週判定・日付判定は `now` 注入で決定論）:
     1. 柱3の値取得を `gate.point_week` でなく `latest_coverage` に差し替える変異
        → 末尾週が未測定のフィクスチャで確定週の値と一致しなくなることを検出できるか
     2. 週の最初の実行判定を常に真にする変異（`week_id` 比較を無条件 `True` に差し替え）
        → 同週の2日目実行でも recompute される（陽性対照が赤くなる）ことを検出できるか
     3. 表示判定の `computed_on == 今日` を外す変異
        → `computed_on` が昨日の payload でも要約が出てしまうことを検出できるか
     4. **（M1）読取障害の判定を「例外が飛んだかどうか」だけにする変異**
        → 入力: 例外を投げずに `measured=False` を返すフィクスチャ（`scripts/lib/measurement_result.py:82-92`・
        `scripts/lib/correction_rate.py:748-757`）。`week_id` が書かれたら失敗とする検査で検出できるか
        （陽性対照は別に置く: 柱3が `measured=True` かつ `point_week is None` の正常フィクスチャでは
        `week_id` が進み「データ蓄積中」になる）
     5. **health を外す変異**（収集関数が形の壊れで `None` を返す）→ `[]`/`null`/`{"week_id": 今週}`/
        `measured: False` の各フィクスチャで Tier1 health が出ないことを検出できるか
   - 実装フェーズで5種を実際に当てて緑のまま残らないことを確認するまで「効いている」と扱わない
     （`verify-checks-by-breaking.md`）。

⑥ **目的の物差しで削る量**: **0**。可視性・到達頻度の話であり、CLAUDE.md の目的文と同じ単位の
直接観測値・算式を持たない。推定・代理指標は使わず 0 と書く。

## 守るべき条件の件数（母集団・取得日つき）

| # | 条件 | 母集団の定義 | 件数 | 取得日 | 扱い |
|---|---|---|---|---|---|
| 1 | 現状、この機構が存在するか | `bin/evolve-daily-run` が書く payload キー一覧（grep） | **0件** | 2026-09-11 | 対象（守る対象そのもの） |
| 2 | pillar2 計算が daily-run 総所要に占める比率 | 下記「実測の一次データ」の総所要と pillar2 秒数の比 | **約1.3%**（8.3秒/616秒） | 2026-09-10 | 参考値（daily-run 側の余力の話） |
| 3 | #379 新設凍結中の新規宣言数 | 本設計が追加提案する新規 store/section/adapter/channel 数 | **0件**（既存 `evolve-queue.json` への新規キーのみ） | 2026-09-11 | 落とす候補（新設なしなので非該当） |
| 4 | 取り下げ候補（REGRESSED）件数 | `optimize_history_store.load_effective_history(slug)` の accepted のうち `verdict=="REGRESSED"` | **0件** | 2026-09-10T23:23:10Z | 落とす候補（③対象外の根拠。issue 本文の明示指示により対象外として残す） |
| 5 | SessionStart 側での重い集計呼び出し | SessionStart が呼ぶ柱2/3/4 関数の数 | **0件**（読むだけ） | 2026-09-11 | 守る対象 |
| 6 | 手動+自動の同日重複実行の発生件数（M3） | daily runner の同日複数回実行ログ件数 | **0件** | 2026-09-11 | 対象外として明記のみ（0件でも issue 本文の運用注意として残す） |

## 方式

`bin/evolve-daily-run` が毎朝、上書き前の `evolve-queue.json` を読み、直前 `weekly_board.week_id`
が今回の ISO 週と異なる（壊れている場合を含む・④参照）ときだけ柱2/3/4を計算し
`payload["weekly_board"]` に書く（precedent: `bin/evolve-daily-run:188-198` の
`payload["llm_judge"]`/`payload["proposals"]`）。SessionStart（`scripts/lib/session_notify/
collectors.py`）は既存の `_resolve_queue_data()`（同ファイル:384-414）が読んだ `queue_data` から
`weekly_board` を取り出し、`computed_on == 今日` のときだけ表示する。計算・書込みは一切しない
（`commit=None`）。

### M1: 読取障害と「データ蓄積中」の見分け方（file:line固定）

| 柱 | 「読取障害」の判定フィールド | 「データ蓄積中（正常）」の判定 |
|---|---|---|
| 柱2 | 外側: `read_measurement()` の戻り値 health `["measured"]`（例外捕捉、`scripts/lib/measurement_result.py:78-92`）。内側: `count_applied_reflections()` 戻り値の `"measured"` キー（`not degraded`、`scripts/lib/pillar2_metrics.py:274-296`。malformed 行・孤立イベント等の品質劣化を検出、例外なしでも False になりうる） | 該当なし（`count=0` は「直近30日に反映0件」という有効な測定結果であり未測定ではない） |
| 柱3 | `build_correction_rate_summary()` の戻り値（`MeasuredDict`）自身の `.measured` 属性（3ストアいずれかの読取失敗、`scripts/lib/correction_rate.py:748-757`） | `.measured is True` かつ `result["gate"]["point_week"] is None`（確定週がまだ0件。読取は健全。契約テスト `scripts/lib/tests/test_correction_rate.py:664-668` `test_point_week_none_when_no_measured_weeks`）→「データ蓄積中」表示 |
| 柱4 | `build_revert_listing()` の戻り値（`MeasuredList`）自身の `.measured` 属性（`read_measurement()` の例外捕捉経由、`scripts/lib/evolve_revert_listing.py:49-51`／`scripts/lib/measurement_result.py:14-28`） | 該当なし（`items=[]` は「戻せる採用0件」という有効な結果） |

判定順序: ①柱2の外側/内側 `measured` いずれか False、②柱3の `.measured` が False、③柱4の
`.measured` が False — いずれか1つでも該当すれば全体を読取障害として扱い `week_id` を進めない
（3値のうち1つでも壊れていれば「今週の要約」として不完全なので全体を再試行に回す。個別の柱
だけ確定させて残りを持ち越す部分成功は本設計では扱わない＝機構を増やさないため）。①②とも
False でなければ柱3の `point_week is None` だけを見て「データ蓄積中」文言に切り替え、`week_id`
は進める。

### M2: 形が壊れているときの扱い

判定と表示は④「黙って消えない」が正典。ここは既存コードとの対応だけを書く。
- **(a) `[]`/`null` など dict でない queue・import 失敗**: 既存の `isinstance(queue_data, dict)` ガード
  （`scripts/lib/session_notify/collectors.py:515`）を使うが、既存の他レーンと違い、**ファイルが実在するのに
  dict でなければ沈黙せず Tier1 health を返す**（既存 resolver は import 失敗を `absent` にするため
  〔同:397-398〕、実在の判定を `Path.exists()` で別に取る）。新しい分類器は作らない
- **(b) `weekly_board` キーが在り dict でない等の形の壊れ・`computed_on` 欠落・`measured: False`**: SessionStart は Tier1 health を
  キー欠落は沈黙する（旧 runner・未実行は形の壊れではない）。
  出す。daily runner は④の妥当性判定で翌日に再計算する（同週中に「今週」の壊れた記録が居座らない）
- **(c) 新しい収集関数の中の例外**: 既存の `try/except` で捕まえたうえで `None` ではなく health を返す
  （同:412-414,470-472 の既存レーンは `None` を返すが、本レーンだけは返さない）

## どの柱がどの PJ の数字か（表示文言に明記・S3）

柱2・柱4は evolve-anything 本体固定（`_PLUGIN_ROOT = Path(__file__).resolve().parent.parent`,
`bin/evolve-daily-run:37`）。柱2は `project_root=_PLUGIN_ROOT`
（`scripts/lib/pillar2_metrics.py:168-175`）、柱4は `slug=pj_slug.resolve_pj_slug(_PLUGIN_ROOT)`
（正準関数、`scripts/lib/pj_slug.py:175`）。柱3は PJ 非依存の全 PJ 横断集計
（`tracked_projects` 既定＝fleet_config 全体）。**表示文言にこの違いを1行明記する**
（例:「柱2・柱4は evolve-anything 本体／指摘率は全PJ合算」）— 全 PJ のセッションで同じ数字が
出る設計である以上、読み手が「今開いている PJ の数字」と誤読しないための最小の対策（新しい
UI 要素は増やさず、`text` と `digest` の両方に同じ1文を足す＝はしご6段目。複数通知時は `digest` が
使われるため〔`scripts/lib/session_notify/merge.py:13-19,39-49`〕、`text` だけでは経路によって消える）。

**非 git 配布時の slug フォールバック**: `resolve_pj_slug` は git 不可のとき basename へ
フォールバックする（`scripts/lib/pj_slug.py:208-221`）。正準関数は固定文字列でなく repo のディレクトリ名を
返すので、**運用前提**として「`_PLUGIN_ROOT` が evolve-anything 本体の git checkout を指し、解決した slug が
採用履歴の保存 slug と一致する」ことに依存する（現在の launchd 設定は本体 checkout を指す＝codex 巡2 で確認）。
前提が崩れた場合は柱4が0件か `measured=False` になり、後者は④の health で見える。検査は足さない
（はしご1段目: 現状0件）。

## 3値の対応と Tier（S1）

| 柱 | 関数 | フィールド | file:line |
|---|---|---|---|
| 柱2 | `pillar2_metrics.count_applied_reflections(project_root, now=...)` | `count` | `scripts/lib/pillar2_metrics.py:168, 294-296` |
| 柱3 | `correction_rate.build_correction_rate_summary(now=...)` | `["gate"]["point_week"]["rate"]`/`["week_id"]` | `scripts/lib/correction_rate.py:678, 665-671` |
| 柱4 | `evolve_revert_listing.build_revert_listing(slug)` | `sum(1 for it in items if it["revert_available"] and not it.get("subsequent_change"))` | `scripts/lib/evolve_revert_listing.py:33, 87-89` |

柱3は `latest_coverage` でなく `gate.point_week` を使う（`latest_coverage` は末尾週が未測定でも
値を返す。`point_week` は末尾未測定なら手前の確定週を採る契約がテストで固定されている——
`scripts/lib/tests/test_correction_rate.py:654-680`
`TestDisplayGatePointWeek::test_point_week_ignores_trailing_unmeasured_week`）。柱4は `now` 引数を
持たない（`scripts/lib/evolve_revert_listing.py:33` 確認済み）ため、陽性/陰性テストは
`load_effective_history` を fake/monkeypatch した固定履歴で日付を作り分ける。

**Tier は Tier1 に確定する**（S1: 根拠を「発火頻度が年52回未満だから」から「同じ日に出るか
出ないかで結果が変わるから」へ修正）。`weekly_board` は「その週で最初に成功した1日だけ」
表示され、翌日には対象外になる（④）。Tier2 の overflow は「発火順に予算内へ入るだけ入れ、
溢れた分は `（ほか: label）` で畳む」契約（`scripts/lib/session_notify/merge.py:42-51`）であり、
その日たまたま Tier1 系統が重なって畳まれれば、**その週の数字を見る機会がその日限りで
失われる**（翌日以降は表示対象外になるため取り戻せない）。実測（下記）では今日のフィクスチャで
Tier1/Tier2 いずれも 167/400字（42%）に収まり overflow は起きないが、「その日を逃すと二度と
出ない」という条件④の性質上、確実性を優先して Tier1 とする。

## 実測の一次データ

対象: 実 DATA_DIR（`~/.claude/evolve-anything/`）・実 PJ に対する読み取り専用呼び出し。

| 項目 | 値 | コマンド／取得時刻 |
|---|---|---|
| 柱2 `count_applied_reflections` 3回 | 8.313 / 6.839 / 5.414秒 | `python3 -c "..."`（下記）／2026-09-10T23:19:58Z |
| 柱3 `build_correction_rate_summary` 3回 | 0.178 / 0.078 / 0.074秒 | 同上／2026-09-10T23:20:03Z |
| 柱4 `build_revert_listing` 3回 | 0.016 / 0.015 / 0.011秒 | 同上／2026-09-10T23:20:09Z |
| daily-run 総所要 | 約616秒（09:00:00起動 → 出力ファイル群 mtime 09:10:13〜16） | `stat -f "%Sm" ~/.claude/evolve-anything/{evolve-queue,icebox-status,icebox-verdicts}.json ~/.claude/evolve-anything/logs/evolve-daily.log`／2026-09-10。`fleet ingest` 単体だけで70.87秒（ログ実測行 `TOTAL: ... elapsed=70.87s`） |
| Tier 圧迫（4系統 realistic fixture + weekly_board digest 39字） | 4系統のみ125字／+weekly_board(Tier1)167字／+weekly_board(Tier2)167字（budget400字） | `python3 - <<'PYEOF' ...`（`hooks/tests/test_restore_state_notification_contract.py::test_today_realistic_four_system_fixture_stays_short` の fixture を再現）／2026-09-10T23:41:06Z |

**v3 でもこれらのコストは SessionStart に一切乗らない**（daily runner 側にのみ発生）。柱2
8.3秒／616秒 ≈ 1.3%（daily-run 総所要に対して無視できる規模）。

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

## 凍結との関係

新規 store / observability section / advisory proposal adapter / weak_signal channel: **0件**。
追加提案は既存の `evolve-queue.json`（宣言: `scripts/lib/store_registry.py:791-808`）への新規
キー `weekly_board` 追加のみ。`shrink_freeze.FROZEN_STORES`（`scripts/lib/shrink_freeze.py:62-112`）
はストア basename の集合であり、既存宣言済みストア内のキー追加はこの集合の要素を増やさない
（`scripts/lib/shrink_freeze.py:1-38, 263-277`）。precedent: `bin/evolve-daily-run:188-198`
（#409 `payload["proposals"]` 追加）。

## 未確定・暫定判断した点

1. 「週の最初の実行」判定のための旧 `evolve-queue.json` 読み込みタイミング（`fleet queue --json`
   実行の前後どちら）は実装フェーズで確定する。
2. 柱2の daily-run 内所要比率（約1.3%）は daily-run 全体が将来軽量化されれば相対的に増える。
   再評価条件は本設計では定めていない。
3. M1 の「3値のうち1つでも読取障害なら全体を再試行」という設計（部分成功を扱わない）は、
   機構を増やさないための簡略化であり、実運用で「柱3だけ頻繁に壊れて他2つが巻き込まれて
   表示されない」事象が出れば見直す。現時点でその発生実績は無い（新設計のため実績0件）。
