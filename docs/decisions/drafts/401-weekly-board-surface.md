# #401: 週1回の戦果ボード要約を SessionStart に自動表示する（v2）

作成日: 2026-09-11（v1: 2026-09-11／v2: 2026-09-11、codex 巡1「設計修正要」を受けユーザー決定
2026-09-11 により方式変更）。対象範囲: issue #401 のうち「ユーザーが何もしなくても戦果ボードの
要約（1〜3行）が目に入る導線」だけ。**設計のみ・実装コードは含まない**。

**v1→v2 の変更点（1行）**: 週1回の dedup を「SessionStart 側の ack marker」で行う案(a)を
codex 巡1「設計修正要」により撤回し、「daily runner がその ISO 週で最初に成功した実行のときだけ
3値を計算し `evolve-queue.json` に書く／SessionStart は読むだけで一切集計・書込みをしない」方式へ
全面変更した。旧案(a)の `evolve-state.json`・marker・lock・pj_slug 別マーカー・SessionStart 同期
集計に関する記述はすべて削除した。

## 完成条件

① **守る対象**: 柱2「実際に反映された改善（直近30日）」の件数・柱3「指摘率」の最新の確定週の
値・柱4「戻せる採用」の件数の要約（1〜3行）が、その ISO 週で daily runner が初めて成功した日
（＝`weekly_board.computed_on`）に、ユーザーが何も操作しなくても SessionStart 通知に自然と現れること
（同じ日にセッションを何回開いても出続け、**翌日以降は出ない**。ユーザー決定 2026-09-11「週の最初の日に出す・
翌日からは消える」）。

② **信頼境界**: 自分たちの実装ミス・性能劣化・既存契約（dry-run 純度・#379 新設凍結・
write barrier）の見落としのみを脅威とする。悪意ある第三者・攻撃者は数えない
（`live-checkout-guard.md` §① と同型の限定）。

③ **対象外**: 取り下げ候補（REGRESSED）の案内（issue 本文の指示どおり。現在0件 — 根拠は
下記「守るべき条件の件数」参照）／柱1（捕捉率）の表示／改善案への y/n 判断 UI／複数 PJ
横断表示（値はプラグイン本体 PJ＝evolve-anything に固定。下記「どの PJ の数字か」参照）／
push 型の非同期通知／表示先を SessionStart 以外に広げること／SessionStart 側での集計・書込み
（daily runner だけが計算・書込みを行う）。

④ **blocking の定義（機械判定できる条件だけ）**:
   - **週の最初の実行でキーが入る**: `evolve-queue.json` の直前内容（上書き前）に保存されている
     `weekly_board.week_id` が今回の ISO 週（`correction_rate.week_id_for(now)`,
     `scripts/lib/correction_rate.py:78`）と異なる（または `weekly_board` 自体が無い／壊れている）
     ときだけ3値を再計算し、`weekly_board.week_id` を今回の ISO 週で書き込む
   - **同週の2日目以降の実行ではキーが変わらない**: 直前内容の `weekly_board.week_id` が今回の
     ISO 週と一致するなら、3値を再計算せず直前の `weekly_board`（`week_id`・`computed_on` を含む）を
     そのまま維持する
   - **表示は計算した日だけ**: SessionStart は `weekly_board.computed_on`（daily runner が計算した日の
     ローカル日付 `YYYY-MM-DD`）が今日のローカル日付と一致するときだけ表示する。一致しなければ何も出さない
     （持ち越した値を週のあいだ出し続けない）
   - **同日2回目の実行でキーが残る**: 手動含め同日に daily runner が複数回走っても、2回目以降は
     直前と同じ週なので上の「2日目以降」と同じ分岐に入り、値が消えたり0にリセットされたりしない
   - **3値が単一ソース関数のフィールドと一致する**: 柱2は `pillar2_metrics.count_applied_reflections`
     の `count`/`measured`（`scripts/lib/pillar2_metrics.py:168, 294-296`）、柱3は
     `correction_rate.build_correction_rate_summary()["gate"]["point_week"]` の `rate`/`week_id`
     （`scripts/lib/correction_rate.py:678, 665-671`。**`latest_coverage` は使わない** — これは
     測定未確定週も含む「直近候補週」であり、確定週を保証する `point_week` と意味が異なる。
     根拠: `scripts/lib/correction_rate.py:713-724` の `latest_coverage` 実装は `weeks[-1]` を
     無条件に採用するのに対し、`compute_display_gate` の `point_week` は末尾が未測定なら
     手前の確定週を採る契約がテストで固定されている＝
     `scripts/lib/tests/test_correction_rate.py:654-680`
     `TestDisplayGatePointWeek::test_point_week_ignores_trailing_unmeasured_week`）、柱4は
     `evolve_revert_listing.build_revert_listing(slug)` の `MeasuredList.measured`
     （`scripts/lib/measurement_result.py:14`）と `revert_available and not subsequent_change`
     の集計（`scripts/lib/evolve_revert_listing.py:87-89` の `revertible_count` と同一のロジック）
   - いずれも `now`（build_correction_rate_summary・count_applied_reflections が対応、
     `scripts/lib/correction_rate.py:678`／`scripts/lib/pillar2_metrics.py:168`）または固定
     history fixture（`build_revert_listing` は `now` 引数を持たない —
     `scripts/lib/evolve_revert_listing.py:33` で確認済み。柱4のテストは `load_effective_history`
     を fake/monkeypatch した固定履歴で日付を作り分ける）で実カレンダーに依存せず単体テストで
     決定論的に再現する。

⑤ **検証方法**:
   - **陽性**: 「直前 `weekly_board.week_id` が先週のまま」の状態で daily runner の書込み関数を
     呼ぶと、今回の ISO 週で `weekly_board.week_id` が更新され、3値が④の各単一ソース関数の
     戻り値と一致することをテストする。
   - **陽性対照**: 「直前 `weekly_board.week_id` が今回と同じ」の状態で呼ぶと、3値も `week_id`
     も一切変化しない（意味を変えない書き換え＝同週内の2回目実行では recompute しない）ことを
     テストする。
   - **陰性試験（2種以上・下記 measure-now 検証と対応）**:
     1. 「柱3の値取得を `gate.point_week` でなく `latest_coverage` に差し替える」変異を当て、
        末尾週が未測定のフィクスチャで確定週の値と一致しなくなることを検出できるか確認する。
     2. 「週の最初の実行判定を常に真にする」変異（`week_id` 比較を無条件 `True` に差し替え）を
        当て、同週の2日目実行でも recompute されてしまう（＝上の「陽性対照」が赤くなる）ことを
        検出できるか確認する。
     3. 「表示判定の `computed_on == 今日` を外す」変異を当て、`computed_on` が昨日の payload で
        SessionStart が要約を出してしまうことを検出できるか確認する（陽性対照: `computed_on` が今日なら出る）。
   - 実装フェーズで上記3種の変異を実際に当てて緑のまま残らないことを確認するまで、この検査は
     「効いている」と扱わない（`verify-checks-by-breaking.md`）。

⑥ **目的の物差しで削る量**: **0**。本設計は「1周の壁時計を短くする」類の時間短縮ではなく、
「週1の数字を能動操作なしで見せる」という可視性・到達頻度の話であり、CLAUDE.md の目的文
（「効果は週1の数字で実感」）と同じ単位の直接観測値・算式を持たない。推定・代理指標は使わず、
素直に 0 と書く。

## 守るべき条件の件数（母集団・取得日つき）

| # | 条件 | 母集団の定義 | 件数 | 取得日 | 扱い |
|---|---|---|---|---|---|
| 1 | 現状、柱2/3/4要約を daily runner が自動計算し表示する機構 | `bin/evolve-daily-run` が `evolve-queue.json` へ書く payload キー一覧（grep） | **0件**（`weekly_board` 相当キーなし） | 2026-09-11 | 対象（守る対象そのもの） |
| 2 | pillar2 計算が daily runner の実測総所要に占める比率 | 下記「所要時間の実測」の daily-run 総所要と pillar2 実測秒数の比 | **約0.9〜1.3%**（8.3秒 / 616秒） | 2026-09-10 | 対象外扱い（SessionStart 予算ではなく daily runner 側の余力の話。参考値として残す） |
| 3 | #379 新設凍結中の新規 store/section/adapter/channel | 本設計が追加提案する新規宣言の数（下記「凍結との関係」） | **0件**（既存 `evolve-queue.json` への新規キー追加のみ） | 2026-09-11（コード確認） | 落とす候補として明記（新設なしなので凍結条項は非該当） |
| 4 | 取り下げ候補（REGRESSED）件数 | `optimize_history_store.load_effective_history(slug)` の accepted のうち `verdict=="REGRESSED"` | **0件** | 2026-09-10T23:23:10Z | 落とす候補（③対象外の根拠。0件だが issue 本文の明示指示により対象外として残す＝0件でも落とさない類型②） |
| 5 | SessionStart 側での重い集計呼び出し | v2 方式で SessionStart が呼ぶ柱2/3/4 関数の数 | **0件**（SessionStart は `evolve-queue.json` を読むだけ） | 2026-09-11（設計確認） | 守る対象（設問2で撤廃したことの確認） |

## 方式（v1 からの変更）

**採用方式**: `bin/evolve-daily-run` が毎朝の実行で、上書き前の `evolve-queue.json` を読み、
その中の `weekly_board.week_id` が今回の ISO 週と異なる（または無い／壊れている）場合だけ
柱2/3/4を計算し `payload["weekly_board"]` として書き込む。同じなら直前の `weekly_board` を
そのまま carry-forward する（`computed_on` も書き換えない）。SessionStart（`scripts/lib/session_notify/collectors.py`）は
既存の `_resolve_queue_data()`（`scripts/lib/session_notify/collectors.py:384-414`）が読み込んだ
`queue_data` から `weekly_board` フィールドを取り出し、`computed_on` が今日のときだけ表示するだけで、一切の計算・書込みを
行わない（`commit=None`）。precedent は `bin/evolve-daily-run:188-198` の
`payload["llm_judge"]`/`payload["proposals"]` 追加と同型。

この変更により、v1 で採用していた `evolve-state.json` への新規キー・marker・lock・
pj_slug ベースの ack はすべて不要になった（daily runner はそもそも単一プロセスの直列実行で
同時書込み競合が起きない。#136 型の cross-PJ 汚染リスクも、値をプラグイン本体 PJ 固定に
することで構造的に消える — 下記「どの PJ の数字か」参照）。

## どの PJ の数字か

プラグイン本体の PJ（evolve-anything）に固定する。daily runner はプラグイン自身の
チェックアウトから起動されるため、`_PLUGIN_ROOT = Path(__file__).resolve().parent.parent`
（`bin/evolve-daily-run:37`、既存定義そのまま）が evolve-anything のリポジトリルートを指す。

- 柱2（`pillar2_metrics.count_applied_reflections`）の `project_root` 引数: `_PLUGIN_ROOT`
  （`scripts/lib/pillar2_metrics.py:168-175` のシグネチャに合わせる）
- 柱4（`evolve_revert_listing.build_revert_listing`）の `slug` 引数:
  `pj_slug.resolve_pj_slug(_PLUGIN_ROOT)`（正準関数、`scripts/lib/pj_slug.py:175`）
- 柱3（`correction_rate.build_correction_rate_summary`）は PJ 非依存の全 PJ 横断集計
  （`tracked_projects` 既定＝fleet_config 全体）なのでそのまま呼ぶ

「どの PJ で作業していても同じ値が出る」ことは、issue #401 のユーザー決定
（「柱2/3/4の要約」＝ `bin/evolve-audit --growth` と同じ関数由来の値）とも整合する — 元々
CLAUDE.md の戦果ボードは対話的にはプラグイン自身のリポジトリで確認するのが主な使われ方
（`bin/evolve-audit --growth` の実行例も `bin/evolve-revert --list` も対象 PJ を指定しない
既定利用が前提）であり、全 PJ で同一の値を見せることは仕様と矛盾しない（ユーザー決定本文の
明示）。

## codex 巡1 指摘への対応

### 指摘1: 表示3値の対応と値の検査

④ blocking の定義に記載したとおり、3値は以下の単一ソース関数のフィールドに固定する（表示側は
これらの値を右から左へ流すだけで独自の再計算をしない）:

| 柱 | 関数 | フィールド | file:line |
|---|---|---|---|
| 柱2 | `pillar2_metrics.count_applied_reflections(project_root, now=...)` | `count`（`measured=False` なら「測定不能・理由」表示に切替） | `scripts/lib/pillar2_metrics.py:168, 294-296` |
| 柱3 | `correction_rate.build_correction_rate_summary(now=...)` | `["gate"]["point_week"]["rate"]`/`["week_id"]`（`point_week is None` なら「測定不能・理由」） | `scripts/lib/correction_rate.py:678, 665-671` |
| 柱4 | `evolve_revert_listing.build_revert_listing(slug)` | `MeasuredList.measured` が False なら「測定不能」。True なら `sum(1 for it in items if it["revert_available"] and not it.get("subsequent_change"))`（＝戻せる件数） | `scripts/lib/evolve_revert_listing.py:33, 87-89`／`scripts/lib/measurement_result.py:14` |

**柱3で `latest_coverage` でなく `gate.point_week` を使う理由**: `latest_coverage`
（`scripts/lib/correction_rate.py:713-724`）は `weeks[-1]`（週リスト末尾）を無条件に採用する
ため、直近候補週がまだ未測定（カバレッジ未100%）でも値を返してしまう。一方 `point_week`
（`compute_display_gate` の戻り値、`scripts/lib/correction_rate.py:646-671`）は末尾が未測定なら
手前の確定週を採用する契約がテストで固定されている
（`scripts/lib/tests/test_correction_rate.py:654-680`
`TestDisplayGatePointWeek::test_point_week_ignores_trailing_unmeasured_week`）。「最新の確定週」
という issue の要求文言（本コーディネータ指示本文）に一致するのは `point_week` である。

### 指摘2: fail-visible

daily runner 側の集計 module import 失敗・計算例外は、他の既存ステップ（`fleet ingest`／
`llm_judge`／`proposal digest`。いずれも `bin/evolve-daily-run` 内で個別 `try/except` して
継続する fail-open 契約、例: `bin/evolve-daily-run` の `proposal digest error` ハンドリング）と
同型で daily-run 全体は落とさない。ただし無音にはしない:

- 計算が例外を投げた場合、`payload["weekly_board"] = {"measured": False, "reason": str(e),
  "generated_at": now.isoformat()}` を書き込む。**この場合は `week_id` を書かない**（＝直前の
  `week_id` と比較する「週の最初の実行」判定が翌日また True になり、自己修復的に再試行される。
  `week_id` を書いてしまうと、直ったのに残りの週ずっと「測定不能」表示が固定されてしまうため）。
- SessionStart 側（`_build_weekly_board_output`、新規実装予定）は、`weekly_board` フィールドが
  存在し `measured=False` であれば Tier1（後述）で「戦果ボード要約の生成に失敗しました
  （理由: …）」を出す。`evolve-queue.json` 自体の absent/corrupt 判定は既存の
  `_classify_daily_snapshot_file`（`scripts/lib/session_notify/model.py:42-56`）と
  `_resolve_queue_data()` の corrupt 分岐（`scripts/lib/session_notify/collectors.py:433-439`）を
  そのまま使う（本設計では新設しない）。

### 指摘3: measure-now 3問 ＋ 陰性試験の対応

`measure-now-not-later.md` の3問（延期する前に自問する項目）を、本設計が保留している論点
（Tier 選定・柱2の daily-run 内比率）に適用する:

1. **今日、自分の権限内でそのデータを生成できないか** — Yes。Tier 圧迫の実測（下記）・柱2/3/4の
   実測秒数・daily-run 総所要はいずれも本セッション内で実行し、下に生値・コマンド・取得時刻を
   記載した（推測・代理指標を使わなかった）。
2. **片側だけでも今出る結論はないか** — Yes。「realistic な4系統フィクスチャに weekly_board
   digest（39字）を足しても 167/400字で収まる」という片側の実測結果は、Tier2 でも今日は破綻
   しないことを示す（下記「Tier 選定の実測」）。
3. **既存データで代理測定できないか** — Yes。柱2/3/4の値・daily-run 総所要はいずれも実
   DATA_DIR／実 launchd ログ（`~/.claude/evolve-anything/logs/evolve-daily.log`）の実測値を使い、
   合成データを作らなかった。

陰性試験（完成条件⑤参照）は、④の2つの blocking 条件それぞれに対応する変異を1種類ずつ用意した
（latest_coverage 誤用／週判定の常時true化）。この2種は「委譲側（コーディネータ）が挙げた回避
手段」と種類が異なる差し替えであり、実装フェーズで両方に緑のまま残らないことを確認するまで
「検査が効いている」とは扱わない。

### 指摘4: Tier の圧迫（実測）

既存の「今日の現実的な4系統フィクスチャ」（`hooks/tests/test_restore_state_notification_contract.py`
の `test_today_realistic_four_system_fixture_stays_short`、drain/queue/judge/icebox の4件）を
再現し、`weekly_board` の digest（39字の想定文言）を Tier1・Tier2 それぞれで追加した場合の
`_merge_notification_text` 出力長を実測した。

```bash
export PYTHONPATH=/Users/matsukaze-takashi/wt/ea-401/scripts/lib
python3 - <<'PYEOF'
from session_notify.model import NotificationItem
from session_notify.merge import _merge_notification_text, TIER2_BUDGET_CHARS
drain = NotificationItem(label="drain", tier=1, text="適用済みの evolve 提案が 1 件あります。",
    digest="記録待ち提案1件（evolve --drain）", tail_link=True)
queue = NotificationItem(label="queue", tier=2, text="evolve 待ち: figma-to-code（1 件）",
    digest="evolve待ち1PJ", tail_link=True)
judge = NotificationItem(label="judge", tier=1, text="llm_judge 日次上限に到達",
    digest="judge持ち越し10311件（自動）")
icebox = NotificationItem(label="icebox", tier=2, text="icebox 58件・最古31日",
    digest="icebox58件・最古31日", tail_link=True)
weekly_digest = "戦果: 柱2 15件/柱3 14.3%(W36)/柱4 12件(戻せる10件)"
base = [drain, queue, judge, icebox]
print(len(_merge_notification_text(base)))
print(len(_merge_notification_text(base + [NotificationItem(label="weekly_board", tier=1, text="…", digest=weekly_digest)])))
print(len(_merge_notification_text(base + [NotificationItem(label="weekly_board", tier=2, text="…", digest=weekly_digest)])))
print(TIER2_BUDGET_CHARS, len(weekly_digest))
PYEOF
```

| ケース | merged 文字数 |
|---|---|
| 4系統のみ（実在の precedent fixture） | 125字 |
| 4系統 + weekly_board（Tier1） | 167字 |
| 4系統 + weekly_board（Tier2） | 167字 |

取得時刻: 2026-09-10T23:41:06Z。`TIER2_BUDGET_CHARS=400`（`scripts/lib/session_notify/merge.py:9`）
に対し、今日の実在データに基づく realistic フィクスチャでは Tier1/Tier2 いずれでも 167/400字
（42%）で収まり、Tier2 でも overflow しない。

**それでも Tier1 を推奨する**: 今日の実測では Tier2 でも安全だが、Tier2 は「発火順に予算内へ
入るだけ入れ、溢れた分は `（ほか: label）` で digest 内容ごと畳まれる」契約
（`scripts/lib/session_notify/merge.py:42-51`）であり、Tier1 系統（trigger／evolve-drain／
data-dir migration等）が同日重なると Tier2 側の残り予算が縮む。weekly_board は「その週の
最初の成功日から翌日の daily-run までは毎回出続ける」という④の blocking 条件を持つため、
1日でも overflow で欠落すると条件④に抵触しうる。週1回しか発火しない（＝Tier1化による他系統
圧迫は年52回未満）ため、確実性を優先して Tier1 とする。

### 指摘5: `build_revert_listing()` に `now` 引数がない

確認済み（`scripts/lib/evolve_revert_listing.py:33` のシグネチャに `now` 無し）。柱4の陽性/陰性
テストは `now` 注入でなく、`load_effective_history` を monkeypatch/fake し、`accepted` エントリの
`timestamp` を固定 fixture で作り分ける方式にする（v1 の「now を1週間進めて注入」という記述は
柱4には適用しない設計へ修正）。

## 所要時間の実測

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

**v2 では上記コストは SessionStart に一切乗らない**（daily runner 側にのみ発生する）。参考として
daily runner の実測総所要に対する比率を出す:

- daily-run 総所要（実測）: 起動時刻 = `~/Library/LaunchAgents/com.evolve-anything.daily.plist`
  の `StartCalendarInterval`（Hour=9, Minute=0）、終了時刻 = 同日の最終出力ファイル
  （`icebox-verdicts.json`／ログ）の mtime。`stat -f "%Sm" ~/.claude/evolve-anything/evolve-queue.json
  ~/.claude/evolve-anything/icebox-status.json ~/.claude/evolve-anything/icebox-verdicts.json
  ~/.claude/evolve-anything/logs/evolve-daily.log` → いずれも 2026-09-10 09:10:13〜09:10:16。
  09:00:00起動 → 終了 09:10:16 ＝ **約616秒**（launchd のスケジューリング誤差込みの概算値）。
  この間 `fleet ingest` 単体だけで実測 70.87秒（ログ実測行:
  `TOTAL: inserted=13227 files=1881 projects=74 elapsed=70.87s`、
  `~/.claude/evolve-anything/logs/evolve-daily.log`）。
- 柱2 8.3秒 / 616秒 ≈ **1.3%**（最大値どうしの比較。平均値 6.9秒なら ≈1.1%）。daily runner の
  総所要に対して柱2追加分は無視できる規模と判断する。

## 凍結との関係の判定と根拠

- 新規 store: **0件**。追加提案は既存の `evolve-queue.json`（宣言:
  `scripts/lib/store_registry.py:791-808`。`kind="json"`／`classification="derived_cache"`／
  writer は daily runner）への新規キー `weekly_board` 追加のみ。`shrink_freeze.FROZEN_STORES`
  （`scripts/lib/shrink_freeze.py:62-112`）はストア **basename** の集合であり、既存宣言済み
  ストア内のキー追加はこの集合の要素を増やさない。
- 新規 observability section / advisory proposal adapter / weak_signal channel: **0件**。
  いずれも追加しない。
- 判定根拠 file:line: `scripts/lib/shrink_freeze.py:1-38`（凍結対象の定義＝4種のみ）、
  `scripts/lib/shrink_freeze.py:263-277`（`assert_no_new_keys` はキー**集合**の差分で判定し、
  対象は `FROZEN_STORES`/`FROZEN_OBSERVABILITY_SECTIONS`/`FROZEN_ADVISORY_PROPOSAL_ADAPTERS`/
  `FROZEN_WEAK_SIGNAL_CHANNELS` の4集合のみ）、`bin/evolve-daily-run:188-198`（既存ストアへの
  フィールド追加の precedent＝#409 `payload["proposals"]` 追加）。
- v1 の `evolve-state.json` への新規キー案は本 v2 では不要になったため撤回する（daily runner
  だけが唯一の writer になり、marker/lock の類が丸ごと不要になったため）。

## 未確定・暫定判断した点

1. **daily runner が「週の最初の実行」判定のために上書き前の `evolve-queue.json` を読む実装**
   （`out_path.write_text` の前に既存ファイルを読む一手間）は設計として妥当だが、具体的な
   読み込みタイミング（`fleet queue --json` の実行前後どちらで読むか）は実装フェーズで確定する。
2. **柱2の daily-run 内所要（8.3秒/616秒≈1.3%）は許容範囲と暫定判断したが、この比率は
   daily-run 自体が今後軽量化された場合に相対的に増える**。将来 daily-run 全体が大幅に短縮
   されたときの再評価条件は本設計では定めていない。
3. **Tier1 化の妥当性**（指摘4）は今日の実測（4系統+weekly_board=167/400字）では Tier2 でも
   安全だが、Tier1 を選んだのは「週1回しか発火しない」という頻度の低さを根拠にした確実性優先の
   判断であり、確定ではない。実装時に既存 Tier1 系統との同時発火頻度をさらに実測してから
   最終決定することを推奨する。
4. **daily runner の失敗時に `week_id` を書かない設計**（指摘2）は「直るまで毎日再試行する」
   ことを優先した判断で、結果として「その週は本当は直っていたのに前日分の値が更新されない
   まま残る」ケースは起きない（週の途中で直れば直った日から3値が入る）。逆に、直らないまま
   週が終わると、その週は一度も weekly_board が表示されないまま次週に入る可能性がある
   （＝blocking 条件④「完全な沈黙」に一見抵触するように見えるが、これは「measured=False の
   Tier1 health notice」自体が表示されている状態であり、③で言う沈黙とは異なる。この区別は
   実装フェーズで単体テストとして固定する）。
