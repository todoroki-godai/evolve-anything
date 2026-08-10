# 修正フィードバック確認詳細（Step 6 / 6.1 / 6.2）

SKILL.md 側には各 Step の見出し・要点・MUST の1行要約のみを残してある。ここは分岐条件・AskUserQuestion テンプレ・コードの正準。

`$PJ` は対象 PJ の絶対パス（束縛パターン: `PJ="${PJ:-$(pwd)}"`。env の `PJ` があれば優先・無ければ cwd。バッチ経路 #400 本体では呼び出し側が `PJ` を env で渡すだけで対応できる）。bash は呼び出しごとに独立プロセスのため、この文書内で `$PJ` を使うコマンド例はいずれも同じ行（または同一コードブロック）内で束縛を再実行してから参照する。

## Step 6: Reflect フェーズ

reflect は独立フェーズではなく discover に統合済み。**`phases.discover.reflect_data_count`**（未処理の修正フィードバック件数）を確認する。前回 reflect 日付は出力に含まれないため、日付ではなく件数で判定する（Step 10.1 も同じ `reflect_data_count` を参照する）。

- `reflect_data_count is None or reflect_data_count < 0`（欠落 or degraded sentinel `-1`・#526-3 / #32）→ discover が失敗して件数を取得できなかった場合。**数値比較する前に「欠落（None）または `< 0`（degraded）」を先に判定する**（discover 全クラッシュ時はキー自体が欠落しうるため `None < 0` の二次クラッシュを避ける。`>= 5` は degraded/欠落 値に対して評価しない）。Report には「discover 失敗のため reflect 件数 不明」と表示し、`phases.discover.error` / `phases.discover.traceback`（#521）を root cause として併記する。AskUserQuestion は出さない（件数不明では判断できないため）
- `reflect_data_count >= 5` → AskUserQuestion で `/evolve-anything:reflect` の実行を提案する（MUST）
  - question: 「未処理の修正フィードバックが {N} 件あります。/reflect を実行しますか？」
  - options: 「実行する」「スキップ」
- `0 < reflect_data_count < 5` → Report に「未処理修正 {N} 件あり」と表示のみ（Step 10.1 のサマリ掲載と整合）
- `reflect_data_count == 0` → スキップ

## Step 6.1: 初回バックログ bootstrap（#443）

既存の weak_signals バックログ（channel ∈ content-rich = llm_judge / rephrase / permission_deny・未昇格・#99）を初回 evolve でまとめて確認する入口。**判定は phase 出力 `result.correction_review.bootstrap` を読むだけで行う（散文ステップで判定しない）。** 機械は「アクティブ PJ」を判定しない — 件数は人間の判断材料として表示するだけ。content-poor チャネル（esc_interrupt / manual_edit_after_ai）は detector 文脈未保存ゆえ対象外（#99）。

- `bootstrap.is_bootstrap != True`（marker 立ち済み or backlog 0 / error）→ **スキップ**（沈黙≠評価のため `bootstrap.is_bootstrap=False` のときのみ「bootstrap: 消化済み ✓」を1行表示）
- `bootstrap.is_bootstrap == True` → **AskUserQuestion で 3 択を人間に選ばせる（MUST — テキスト表示だけで済ませない）**。question に `bootstrap.pj_total` 件・`bootstrap.groups_total` グループを判断材料として提示する。**各 option の `detail` に下記の副作用1行を必ず添える（MUST）** — 3択は「marker を立てるか立てないか」で以後の再表示挙動が非対称になり、取り違えると bootstrap が永久に消える / 永久に再提示される（#51 MEDIUM）:
  - question: 「この PJ の未昇格バックログ {pj_total} 件（{groups_total} グループ）を初回 bootstrap で消化しますか？」
  - options（`detail` に副作用を明示する）:
    1. **まとめて確認** → 〔副作用〕確認完了後に `mark_done` で完了 marker（`bootstrap_done-<slug>.marker`）が立ち、**以後この PJ で bootstrap は再表示されない**。確認しなかった残りは weak_signals の TTL（45日・`weak_signals/ttl.py` の `TTL_DAYS`）で自然失効する。提示方式は `bootstrap.theme_buckets` の有無で分岐する（#558。`theme_buckets` は group 数が `THEME_CLUSTER_THRESHOLD`（=12）超のときだけ phase が emit する決定論 TF-IDF テーマクラスタ。閾値以下は `None`）:
       - **`bootstrap.theme_buckets` が非 None（= group 数が閾値超）→ バケット単位の multiSelect 1 問に畳む（MUST。質問マラソンを避け explain-clearly と整合させる）。** 各バケット `{theme_label, group_indices, groups}` を AskUserQuestion の multiSelect オプション 1 個として提示し（label に `theme_label` と件数）、ユーザーが選んだバケットに含まれる全 group の `signal_keys` をまとめて `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --promote-weak <signal_keys カンマ区切り>` で一括昇格する（選ばれなかったバケットは昇格しない）。バケット内 group の `confirmable_idiom` / `cross_pj_confirmed` は下記 per-group と同じ扱い（非 None idiom は confirmed 化される旨を multiSelect の説明に添える）。
       - **`bootstrap.theme_buckets` が None（= group 数が閾値以下）→ 従来の per-group フロー（挙動不変）。** `bootstrap.groups` を順に AskUserQuestion バッチで提示（各 group の `representative` を確認 → 承認なら同 group の `signal_keys` を `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --promote-weak <signal_keys カンマ区切り>` で一括昇格）。group の `confirmable_idiom` が非 None なら「確定すると idiom『{confirmable_idiom}』も confirmed 化（以後この表現の再発を自動昇格）」を question に添える（None＝過汎用 FP guard #527 で除外済み・standing auto-promote rule にしない・#527-4）。group の `cross_pj_confirmed` が非空なら「他 PJ（{slug一覧}）で承認済み」を question に添える（判断材料の提示のみで自動承認はしない）。
       いずれの方式でも CLI が promote と同時に対応 idiom を confirmed=True 化する（#463 — `promote_signals` ライブラリ直接呼びは confirmed 化をバイパスするため使わない）。出力の `skipped` が空でなければ（#326）どの signal_key が昇格されなかったか・理由（`not_found` / `already_promoted` / `expired` / `already_reviewed`）を report に残す（bootstrap は per-key 既読追記をしないため `record_reviewed` の呼び分けは不要だが、部分失敗を沈黙させない）。確認完了後に `bootstrap_backlog.mark_done(slug, dry_run=dry_run)` で marker を立てる。
    2. **日次5件ずつ** → 〔副作用〕**marker を立てない**ため、以後の evolve でも `is_bootstrap=True` が**再提示され続ける**（Step 6 の通常 reflect ページネーションに合流。少しずつ消化したいとき向き）。
    3. **TTL 失効に任せる** → 〔副作用〕`bootstrap_backlog.mark_done(slug, dry_run=dry_run)` で完了 marker が立ち、**以後 bootstrap を再提示しない**（＝今回は1件も確認しないまま打ち切る）。残りは weak_signals の TTL（45日・`weak_signals/ttl.py` の `TTL_DAYS`）が間引く。option 1 と「marker を立てる＝再表示されない」点は同じで、違いは**確認するか/しないか**。

`mark_done` は `dry_run=True`（ドライラン実行時）なら marker を書かない（最下層まで dry-run ゲートを貫通）。3 択いずれを選んでも、Skip しても evolve 全体は完走する。

`bootstrap_backlog` は `correction_semantic` パッケージ配下なので、Step 6.5 と同じく sys.path を通してパッケージから import する（`import bootstrap_backlog` 直 import は ModuleNotFoundError になる）:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from correction_semantic import bootstrap_backlog

# #492: slug は phase 出力（build が実際に read に使った slug）をそのまま渡す。
# ここで resolve_slug() を再導出すると、評価が project_dir != cwd や repo subdir / worktree
# から起動された場合に build と別 slug を解決し、marker が別ファイルになって bootstrap が
# 永久再提示される（read/write split-brain）。read=write の slug を構造的に保証する。
slug = result["correction_review"]["bootstrap"]["slug"]

# 「まとめて確認」完了時・「TTL 失効に任せる」選択時のどちらでも呼ぶ。
# dry_run=True（ドライラン実行時）なら marker を書かない。
res = bootstrap_backlog.mark_done(slug, dry_run=dry_run)
# res == {"written": bool, "dry_run": bool, "path": str}
```

## Step 6.2: 今日の修正確認（daily_review・#446）

前回 evolve 以降の**新規** weak_signal（channel ∈ content-rich = llm_judge / rephrase / permission_deny・未昇格・非expired・既読集合に無いもの・#99）を idiom 単位で確認する日次入口。reflect SKILL Step 7.7 の散文ステップからの移植（learning_skill_md_must_not_enforcement — 毎日叩かれる evolve の決定論 phase 出力を消費する）。**判定は phase 出力 `result.correction_review.daily` を読むだけで行う。** content-poor チャネル（esc_interrupt / manual_edit_after_ai）は detector が周辺文脈を保存せず y/n 確認の判断材料が無いため対象外で、observability の weak_signals matrix に件数として残る（#99）。

**二重提示の解消（#476-3）**: Step 6.1 で `bootstrap.is_bootstrap == True` の run では、daily phase は bootstrap groups が保持する signal_key を自動的に除外して emit する（evolve.py が `exclude_signal_keys` で配線済み）。そのため Step 6.1（まとめて確認）→ Step 6.2 を順に実行しても同じシグナルを 2 回質問しない。`daily.remaining` も bootstrap-pending を除いた「前回以降の新規」だけを数える。

- `daily.eligible != True`（新規 0 件 / error）→ **スキップ**（AskUserQuestion を出さない。`daily.eligible == False` のときのみ「今日の修正確認: 新規なし ✓」を1行表示）
- `daily.eligible == True` → `daily.groups`（最大5件・cross-PJ 承認済み一致が先頭、続いて頻度降順 — #462）を **AskUserQuestion で y/n 確認（MUST — 最大5問を1バッチで）**。各 question に group の `idiom`（無ければ `representative`）と `evidence.count`（再発回数）を提示し、`confirmable_idiom` が非 None なら「『はい』で確定すると以後この表現の再発を自動昇格する idiom『{confirmable_idiom}』も confirmed 化される」を添える（None＝過汎用 FP guard #527 で除外済み・この group の昇格は今回限りで standing auto-promote rule にならない・#527-4）。`cross_pj_confirmed` が非空なら「他 PJ（{slug一覧}）で承認済み」も添える（判断材料の提示のみで自動承認はしない）:
  - **はい（昇格）** → 同 group の `signal_keys` を `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --promote-weak <signal_keys カンマ区切り>` で昇格（CLI が promote と同時に対応 idiom を confirmed=True 化し、以後の同テキスト再発は idiom_autopromote が機械昇格する — #463。出力の `confirmed_idioms` 件数で確認可。出力の `corrections_human_allpj` は昇格後の全PJ集計 human-confirmed 件数（**per-PJ の growth_report.corrections_human とは別物 — #557**。Step 9 の成長状態表示には使わない — [report-narration.md](report-narration.md) の対話前スナップショット問題補正を参照）→ **promote 成功を signal_key 単位で確認してから既読追記する（MUST・#326）**: 出力の `promoted_keys`（実際に昇格された signal_key 一覧）と `skipped`（昇格されなかった signal_key + 理由。`reason` ∈ `not_found` / `already_promoted` / `expired` / `already_reviewed`）を読み、`daily_review.record_reviewed(...)` には **`promoted_keys` のみ**を渡す（`requested` と `promoted` の件数差＝`skipped` が空でないときが部分失敗）。`skipped` の signal_key は既読追記しない（取りこぼし防止 — 次回再提示される。`reason=expired` は理論上ほぼ出ない — daily/bootstrap の候補提示自体が TTL 失効を read 時導出で除外するため）
  - **いいえ（却下）** → `daily_review.record_reviewed(signal_keys, slug, decision="rejected", dry_run=dry_run)` で既読追記（次回から再提示しない）
  - **Skip / Other / 中断** → 既読追記しない（次回再提示）。evolve 全体は完走する
- `daily.remaining > 0` なら「ほか {remaining} グループは次回以降に提示」を1行表示する

`record_reviewed` は `dry_run=True`（ドライラン実行時）なら既読集合に書かない（最下層まで dry-run ゲートを貫通）。dry-run では確認の表示のみ行い、promote / 既読追記は行わない。

`daily_review` も `correction_semantic` パッケージ配下なので、パッケージから import する（`import daily_review` 直 import は ModuleNotFoundError になる）。`decision` はキーワード専用引数:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from correction_semantic import daily_review
# #492: slug は phase 出力（build_review が実際に read に使った slug）をそのまま渡す。
# resolve_slug() の再導出は read/write split-brain（既読除外不発）の原因になる。
slug = result["correction_review"]["daily"]["slug"]

# はい＝昇格の場合: --promote-weak の出力 promote_res の promoted_keys のみ既読追記する（#326）。
# promote_res["skipped"]（{"signal_key": ..., "reason": ...}）が非空なら部分失敗＝その
# signal_key は既読追記しない（次回再提示。requested と promoted の件数差がそのまま skipped 件数）。
res = daily_review.record_reviewed(
    promote_res["promoted_keys"], slug, decision="promoted", dry_run=dry_run
)
# 却下時（いいえ）は promote を呼ばず signal_keys 全件を decision="rejected" で既読追記する。
# res == {"written": int, "dry_run": bool}
```
