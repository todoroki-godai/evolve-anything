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
- **`bootstrap.excluded_machinery_total > 0`（machinery＝委譲メッセージ等の harness 注入除外・#443 PR2-a）→ 上記スキップ／下記 AskUserQuestion のどちらの分岐でも「除外: machinery {excluded_machinery_total} 件（委譲メッセージ等の harness 注入。実際に確認可能な件数には含まれていません）」を必ず1行添える（MUST — silence != evaluated）。** 候補が全件 machinery だと `pj_total` / `groups_total` が 0 になり、3択も「消化するものが無い」空提示になる。この1行が無いと利用者には除外の事実が完全に隠れる（最重要ケース）。なお `is_bootstrap` は marker の有無だけで決まる（backlog 0 でも marker 未設定なら `True`）。
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

## Step 6.2: 今日の修正確認（daily_review・#446・#514）

前回 evolve 以降の**新規** weak_signal（channel ∈ content-rich = llm_judge / rephrase / permission_deny・未昇格・非expired・既読集合に無いもの・#99）を idiom 単位で確認する日次入口。reflect SKILL Step 7.7 の散文ステップからの移植（learning_skill_md_must_not_enforcement — 毎日叩かれる evolve の決定論 phase 出力を消費する）。**判定は phase 出力 `result.correction_review.daily` を読むだけで行う。** content-poor チャネル（esc_interrupt / manual_edit_after_ai）は detector が周辺文脈を保存せず y/n 確認の判断材料が無いため対象外で、observability の weak_signals matrix に件数として残る（#99）。

**#514: 修正在庫（`daily.correction_backlog`）は `daily.eligible` と独立に判定する。** `eligible` は新規 weak_signal の有無だけを表すフィールドで、在庫（それ以前に `--promote-weak` 済みで反映先未定のまま溜まった記録）の有無を含まない。新規が 0 件でも在庫が非空なら在庫3択は出す（黙って両方スキップしない）。

**二重提示の解消（#476-3）**: Step 6.1 で `bootstrap.is_bootstrap == True` の run では、daily phase は bootstrap groups が保持する signal_key を自動的に除外して emit する（evolve.py が `exclude_signal_keys` で配線済み）。そのため Step 6.1（まとめて確認）→ Step 6.2 を順に実行しても同じシグナルを 2 回質問しない。`daily.remaining` も bootstrap-pending を除いた「前回以降の新規」だけを数える。

- `daily.eligible != True and not daily.correction_backlog`（新規 0 件・在庫 0 件 / error）→ **スキップ**（AskUserQuestion を出さない。「今日の修正確認: 新規なし ✓」を1行表示）
- **`daily.excluded_machinery_total > 0`（machinery＝委譲メッセージ等の harness 注入除外・#443 PR2-a）→ 上記スキップ／下記 AskUserQuestion のどちらの分岐でも「除外: machinery {excluded_machinery_total} 件（委譲メッセージ等の harness 注入。実際に確認可能な件数には含まれていません）」を必ず1行添える（MUST — silence != evaluated）。** 候補が全件 machinery で `daily.eligible` が `False` になったケースこそ、この1行が無いと利用者には「今日の修正確認: 新規なし ✓」しか見えず除外の事実が完全に隠れる（最重要ケース）。
- `daily.correction_backlog` が非空 → **在庫3択で確認する（MUST）**。手順は下記「修正在庫の3択（#514）」を参照（`daily.eligible` の真偽に関わらず実施する）。
- `daily.eligible == True` → `daily.groups`（`correction_backlog` の件数分だけ枠を削った残り・cross-PJ 承認済み一致が先頭、続いて頻度降順 — #462）を1件ずつ**反映先つき4択で確認する（MUST — #475。在庫3択と合わせて最大5問を1バッチで）**。手順は下記「反映先つき4択（#475 §4）」を参照。
- `daily.remaining > 0` なら「ほか {remaining} グループは次回以降に提示」を1行表示する
- `daily.correction_backlog_remaining > 0` なら「ほか {correction_backlog_remaining} 件は在庫に残っています」を1行表示する

### 反映先つき4択（#475 §4）

各 group について、**AskUserQuestion を呼ぶ前に agent が反映候補の起草行（`draft_line`）を作る**（§4.3。誰が書くかは常に agent の Edit/Write なので、順番を「選ばせた後」から「選ばせる前」に変えるだけ）。

**0. 重複チェック（§4.5・設問枠を消費しない）**: `draft_line` を起草したら、既存の書き込み規約（[reflect/SKILL.md](../../reflect/SKILL.md) の「書き込み時のルール」）と同じ判断で候補ファイル（既存 rule ファイルのうち内容が近いもの）を1つ選び、`reflect_apply_match.check_line_applied` で正規化後一致を確認する:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from reflect_apply_match import check_line_applied
result = check_line_applied(Path(candidate_file), draft_line)  # candidate_file は agent が選ぶ
```

一致すれば（`result["matched"] is True`）AskUserQuestion を出さず「この指摘は既に `{candidate_file}` に反映済みでした」と1行報告し、`PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --promote-weak <signal_keys> && evolve-reflect --apply <source_correction_id> --target-path <candidate_file> --draft-line-file <draft_line を書いたファイル>` を実行して `reflect_status` を直接 `applied` に更新する（§6.1 の同じゲートを通す）。一致しなければ下記の設問へ進む。

**1. 設問**（`{idiom または representative}` は group の `idiom`（無ければ `representative`）、`{count}` は `evidence.count`。`cross_pj_confirmed` が非空なら「他PJ（{slug一覧}）で承認済み」を1文追加する）:

```
「{idiom または representative}」（{count}回{、他PJ（slug…）で承認済み}）

書く文面（案）: {draft_line}

この指摘を、どこに反映しますか？
メモや落とし穴集に残したい場合は Other に記入してください。
```

**options（固定4択・順番を機械に決めさせない。label/detail はそのまま出す — § 番号・file:line・内部語を出さない）**:

| # | label | detail |
|---|---|---|
| 1 | 共通ルールに書く（全PJで効く） | 次のセッションから全プロジェクトで効きます。あとで1コマンドで取り消せます（条件は反映時に表示）。 |
| 2 | このPJのルールに書く | 次のセッションからこのプロジェクトだけで効きます。取り消しも同様です。 |
| 3 | いまは反映しない（記録は残す） | 動作は変わりません。記録は消えず、5件たまったら見直しをまとめて案内します。 |
| 4 | いいえ（この指摘は不要） | 記録も反映もしません。次回から出しません。 |

Other は tool が自動付与する自由記述欄（`options` には含めない）。

**2. 選択後の処理**:

- **1（共通ルール）/ 2（PJルール）**:
  1. `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --promote-weak <group の signal_keys カンマ区切り>` で昇格する（従来どおり。出力は `promoted_keys`/`skipped`/`confirmed_idioms` を含む — 部分失敗時の扱いは下記「既読化」参照）。
  2. 反映先ファイルを agent が判断する（既存の書き込み規約を流用・新しい選定ロジックは作らない）。**選んだ scope（共通=`~/.claude/rules/` / PJ=`<repo>/.claude/rules/`）の中に適切な既存ファイルが無く新規作成が必要と判明したら**、Edit/Write の**直前**に3択の追加確認を出す（§4.3.2）:

     | # | label | detail |
     |---|---|---|
     | 1 | 新しく作る | このファイルは無いので新規作成します。**取り消せません**。 |
     | 2 | 既存の `<候補ファイル>` に追記する（推奨） | 取り消せます。テーマが近い既存ファイルに1行追記します |
     | 3 | やめる（記録だけ残す） | 反映しません。選択肢3と同じ扱いになります |

     既存ファイルへの追記なら（1/2いずれの結果でも）この追加確認はスキップしてそのまま書く。3を選んだ場合は下の「3（いまは反映しない）」と同じ処理に切り替える。
  3. Edit（既存ファイル末尾に1行追記）または Write（新規ファイル）で `draft_line` を書き込む。**Edit/Write の前に、対象ファイルの現在の全文を読み一時ファイルに保存しておくこと（MUST）**（新規作成なら空ファイルを保存する）。反映先が `~/.claude/rules/` または `<repo>/.claude/rules/` 配下のときは次の手順4で `--before-content-file` が**必須**になる（省略すると CLI がエラーで停止する — 取り消し記録の欠落を黙認しないための仕様）。
  4. `evolve-reflect --apply <source_correction_id> --target-path <書き込んだファイル> --draft-line-file <draft_line を書いたファイル> --before-content-file <手順3で保存した全文>` を呼び、実在確認を通過したことを確認する（`status == "applied"`）。`source_correction_id` は `--promote-weak` の出力に対応する correction の `session_id`/`timestamp` から `make_source_correction_id` で作る（`--view` 出力の `source_correction_id` と同一形式）。`apply_unverified` が返ったら書き込みに失敗している可能性があるため対象ファイルを再確認する。**新規作成（空ファイルを渡した）のときは `status == "applied"` でも `revert_recorded: false`（`revert_reason: "new_file_not_revertible"`）が返る** — これは正常（新規ファイル作成は取り消し非対応・§8.2「やらないこと」）。
  5. 反映が完了したら「反映しました: `{target}`（1行追記）／取り消す場合: `bin/evolve-revert <entry_id>`／※このファイルをこの後さらに変更すると、この取り消しはできなくなります」を1行表示する（§4.3.1。新規作成のときは「取り消せません」に置き換える）。
  6. 既読化: 出力の `promoted_keys`（実際に昇格された signal_key）のみを `daily_review.record_reviewed(promoted_keys, slug, decision="promoted", dry_run=dry_run)` に渡す（`skipped` が空でなければ部分失敗＝#326 と同じ扱いで既読追記しない）。
- **3（いまは反映しない）**: `--promote-weak` で昇格するところまでは同じ（`reflect_status` は `promoted` のまま反映先ファイルへは書かない）。`daily_review.record_reviewed(promoted_keys, slug, decision="deferred", dry_run=dry_run)` で既読追記する（**次回の evolve では再提示しない**。保留は reflect のバッチレビューへ再浮上する — §5.1 / [reflect/SKILL.md](../../reflect/SKILL.md)）。
- **4（いいえ）**: `--promote-weak` を呼ばない。`daily_review.record_reviewed(signal_keys, slug, decision="rejected", dry_run=dry_run)` で既読追記する（次回から再提示しない）。
- **Other（skill / hook を書かれた場合の応答・§4.4）**: 自由記述の内容で分岐する。memory 関連→ 既存の memory 反映フロー（Step 7）へ。pitfall 関連 → 既存の pitfall-curate フロー（#471）へ。**skill 関連** → 「この場では反映されません。skill を直す場合は `/evolve-anything:evolve-skill <名前>` を実行してください」と案内する（既読追記しない・次回再提示）。**hook 関連** → 「hook への反映は自動化されていません。必要なら `hooks/` を手で編集してください」と案内する（既読追記しない）。**判断が付かない** → 「memory と pitfall のどちらに書きますか？」等、その場で1回だけ聞き返す（黙って対象外にしない）。
- **Skip / 中断** → 既読追記しない（次回再提示）。evolve 全体は完走する。

`record_reviewed` は `dry_run=True`（ドライラン実行時）なら既読集合に書かない（最下層まで dry-run ゲートを貫通）。`--dry-run` 実行では確認の表示のみ行い、promote / Edit・Write / 既読追記のいずれも行わない。

`daily_review` / `reflect_apply_match` はいずれも `scripts/lib` 配下の module なので sys.path を通してから import する（直 import は ModuleNotFoundError になる）。`decision` はキーワード専用引数:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from correction_semantic import daily_review
# #492: slug は phase 出力（build_review が実際に read に使った slug）をそのまま渡す。
# resolve_slug() の再導出は read/write split-brain（既読除外不発）の原因になる。
slug = result["correction_review"]["daily"]["slug"]

# 1/2（反映した）場合: promote_res の promoted_keys のみ既読追記する（#326）。
res = daily_review.record_reviewed(
    promote_res["promoted_keys"], slug, decision="promoted", dry_run=dry_run
)
# 3（いまは反映しない）の場合: decision="deferred" で既読化するが reflect_status は
# promoted のまま反映先へは書かない（#475 §5.1）。
res = daily_review.record_reviewed(
    promote_res["promoted_keys"], slug, decision="deferred", dry_run=dry_run
)
# 4（いいえ）の場合は promote を呼ばず signal_keys 全件を decision="rejected" で既読追記する。
# res == {"written": int, "dry_run": bool}
```

### 修正在庫の3択（#514）

`daily.correction_backlog` の各アイテム（`{source_correction_id, message, age_days, timestamp, session_id}`）を古い順に確認する。**新規4択と違い、これらは既に `reflect_status=promoted`（過去に承認済み・反映先だけが未定のまま溜まった記録）なので `--promote-weak` は呼ばない。**

**1. 起草 → 重複チェック**（§4.5 と同じ手順を流用。新しい判定ロジックは作らない）: `message` を元に agent が反映候補の起草行（`draft_line`）を作り、既存の書き込み規約（[reflect/SKILL.md](../../reflect/SKILL.md) の「書き込み時のルール」）に沿って候補ファイルを1つ選び `reflect_apply_match.check_line_applied` で正規化後一致を確認する。一致すれば AskUserQuestion を出さず「この指摘は既に `{candidate_file}` に反映済みでした」と1行報告し、`PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --apply <source_correction_id> --target-path <candidate_file> --draft-line-file <draft_line を書いたファイル>` を実行して `reflect_status` を直接 `applied` に更新する。一致しなければ下記の設問へ進む。

**2. 設問**（`{message}` は在庫アイテムの `message`、`{age_days}` は同 `age_days`）:

```
「{message}」（{age_days}日前から在庫）

書く文面（案）: {draft_line}

この指摘を、どこに反映しますか？
```

**options（固定3択・順番を機械に決めさせない）**:

| # | label | detail |
|---|---|---|
| 1 | 共通ルールに書く（全PJで効く） | 次のセッションから全プロジェクトで効きます。あとで1コマンドで取り消せます（条件は反映時に表示）。 |
| 2 | このPJのルールに書く | 次のセッションからこのプロジェクトだけで効きます。取り消しも同様です。 |
| 3 | もう出さない | 記録は残りますが、以後この指摘は在庫からもう出てきません。 |

**3. 選択後の処理**:

- **1（共通ルール）/ 2（PJルール）**:
  1. 反映先ファイルを agent が判断する（既存の書き込み規約を流用・新しい選定ロジックは作らない）。**選んだ scope（共通=`~/.claude/rules/` / PJ=`<repo>/.claude/rules/`）の中に適切な既存ファイルが無く新規作成が必要と判明したら**、Edit/Write の直前に §4.3.2 と同じ3択の追加確認を出す（新しく作る／既存の候補ファイルに追記する（推奨）／やめる＝下の「3（もう出さない）」と同じ処理に切り替える）。既存ファイルへの追記ならこの追加確認はスキップしてそのまま書く。
  2. Edit（既存ファイル末尾に1行追記）または Write（新規ファイル）で `draft_line` を書き込む。**書き込みの前に、対象ファイルの現在の全文を読み一時ファイルに保存しておくこと（MUST）**（新規作成なら空ファイルを保存する）。反映先が `~/.claude/rules/` または `<repo>/.claude/rules/` 配下のときは次の手順で `--before-content-file` が**必須**になる（省略すると CLI がエラーで停止する）。
  3. `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --apply <source_correction_id> --target-path <書き込んだファイル> --draft-line-file <draft_line を書いたファイル> --before-content-file <手順2で保存した全文>` を呼び、`status == "applied"` を確認する。
     - **`apply_unverified` が返ったとき（復旧手順・MUST — 無限再提示に対する唯一の歯止め）**: 書き込んだ内容と `--draft-line-file` の中身が一致していない可能性が高い。① 対象ファイルを再確認し、必要なら Edit で `draft_line` に一致させる → ② 同じ `source_correction_id` で `--apply` を再実行する。② の結果も `apply_unverified` なら、これ以上リトライせず選択肢3（もう出さない）の処理（`--skip <source_correction_id>`）に切り替える（`reconcile_surfaced` 相当の自動却下 marker は作らない — #379 Step1 新設凍結）。
  4. 反映が完了したら「反映しました: `{target}`（1行追記）／取り消す場合: `bin/evolve-revert <entry_id>`／※このファイルをこの後さらに変更すると、この取り消しはできなくなります」を1行表示する（新規作成のときは「取り消せません」に置き換える）。
- **3（もう出さない）**: `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --skip <source_correction_id>` を呼ぶ（`--apply` と同じ `--dry-run` 規約 — ドライラン実行時のみ書かない）。既に `applied` 済みの記録には CLI がガードを掛けて上書きしない（`status == "already_applied"`）。
- **Skip / 中断** → 何も呼ばない（次回の evolve でも同じ在庫が再提示される）。

**在庫の中身についての注意（#514 実測 2026-08-18）**: 在庫の約10%は15文字以下の相槌（「推奨で」「お願い」等）、約8%は Claude 自身の出力の混入で、機械的な有用性判定はしていない（一括破棄もしない設計）。**最初の数日はこれらに「もう出さない」を選ぶ運用になる見込み** — 想定内の挙動であり、異常ではない。
