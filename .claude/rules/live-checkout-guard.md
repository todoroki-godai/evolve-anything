# 共有 checkout はユーザー実行環境（作業ブランチを実行環境にしない）

**完成条件（`review-round-cap.md` の定型。この外側は本変更で扱わない）**
①**守る対象**: ユーザー実行環境がレビュー未了のコードを実行している状態が、人間に気づかれないまま継続すること
②**信頼境界**: 脅威に数えるのは**自分たちの運用ミス**のみ。悪意ある第三者・攻撃者・意図的な配置改変は数えない
③**対象外**: 防止・block・強制。セッション途中の切替の即時検出。参照先を作業ブランチから切り離す構造是正（`#548`）
④**blocking**: 「非既定ブランチ／dirty／ahead の実環境で、SessionStart に何も出ない」または「判定不能なのに無音」
⑤**検証方法**: 陽性・陽性対照・判定不能の3件を実測（本文末尾）

- **事実（2026-08-25 実測）**: 実行時の plugin root は共有 checkout。根拠を2つ揃えて確認した:
  ①`known_marketplaces.json` の当該 marketplace が `source.source = "directory"` / `installLocation` = 共有 checkout
  ②本日の SessionStart 出力が共有 checkout 配下のパスを印字し、それは
  `scripts/lib/daily/proposal_digest.py:573-583` が実行時 plugin root から逆算して組んでいる。
  **registry だけでは証明にならない** — `installed_plugins.json` の `installPath` は cache
  （mtime 2026-08-18 で stale）を指し、2つのレジストリは食い違う
- **実行 root は「呼出側が渡す」。`Path(__file__)` を単独の根拠にしない**。`__file__` はモジュールの
  物理位置にすぎず、cache へのコピー・ファイル単体 symlink・`PYTHONPATH` 先頭差し替え・
  worktree の wrapper が主 checkout を import する配置で、**実際に動いている木とは別の木を指す**。
  **hook・audit の各呼出側が「自分が属すると期待する plugin root」を明示的に渡し、
  `live_checkout` は ①呼出元の `__file__` ②モジュール自身の `__file__` ③渡された root の
  3つが同一の木に属するかを照合する。1つでも別の木なら「判定不能」を返す**
  （worktree の wrapper が clean な共有 checkout のモジュールを import すると、
  照合が1つだけでは通ってしまい、未レビューの wrapper が動いているのに無音になる）。
  registry の値も照合用の副次情報として使い、食い違いは別警告にする
- **ブランチ名だけで安全と判定しない**。Python が実行するのは HEAD ではなく **working tree**。
  判定は次の **OR**: ①既定ブランチでない ②tracked file が dirty ③解決済みの既定ブランチ
  （`origin/<resolved-default>`。**`origin/main` に固定しない** — `master`/`trunk` で誤判定する）に対して
  **ahead**（既定ブランチかつ clean でも、未 push・未レビューの local commit は同じ害を持つ。
  `git rev-list --left-right --count origin/<resolved-default>...HEAD` の右側が非0）
- **既定ブランチが確定できないときは「安全」と扱わない**。`origin/HEAD` が取れないとき `main` を
  仮定すると `master`/`trunk` の repo で誤警告が出る。かといって無音にすると
  `git remote set-head origin -d` した非既定ブランチが見逃される。
  **SessionStart に低強度の「判定不能・理由」を出す**（警告と同じ強さにはしない）
- **fail-open は「作業を止めないこと」だけに適用する。検出系の健全性は fail-visible にする**。
  **`bin/evolve-audit` だけに出すのは不足**（人が実行しなければ無音）。
  **hook と audit の各呼出側が import 失敗・実行失敗を独立に捕捉し、SessionStart にも health を出す**。
  同じモジュールを両方が import する以上、構文エラーは共通原因障害になる — **捕捉を呼出側に置く**
- **`#379` の新設凍結を守る**。新 store も新 weak_signal channel も作らない。状態は永続化せず read 時導出。
  ゆえに**継続期間の表示と ack は実現しない**（`first_seen` を保持できないため）。
  代わりに**既存 `NotificationItem` の同一キー集約で SessionStart ごとに1件へ畳み**、
  branch 名・dirty 件数・ahead 件数・**復旧コマンド**だけを固定表示する
- **判定ロジックの単一ソースは `scripts/lib/live_checkout.py`**。通知は既存 `NotificationItem` 集約契約に
  乗せる（`hooks/restore_state.py` から別 JSON 行を直接出さない）
- **この rule は強制力を持たない。守らなくても何も赤くならない**。SessionStart 警告は事後通知であって
  「共有 checkout で自動到達コードを編集した」という違反の検出ではない。
  **セッション途中の切替は次の SessionStart まで検出されない**。この限界を承知の上で、
  気づく手段をゼロから1にすることを目的とする
- **例外を置くときは owner・理由・開始時刻・終了条件を書く**。**既定ブランチへ戻すのは、PR マージ後だけでなく、
  PR 中止・レビュー差戻し・単発検証の終了時・離席前・セッション終了前にも行う**
- **測定は実装の完了条件に含める（延期しない）**: ①**陽性** — 共有 checkout が現に非既定ブランチかつ
  dirty な今日この状態で警告が出ること ②**陽性対照** — 既定ブランチ・clean・ahead 0 の fixture repo で
  警告が出ないこと ③**判定不能** — JSON 不正／git 不在／`origin/HEAD` 削除の各ケースで
  SessionStart に「判定不能・理由」が出ること。**3件とも実測結果を報告するまで完了としない**
- **未実測**: 警告が人間に実際に表示され、既定ブランチへ戻す行動に繋がるかは測っていない
  （hook stdout に出ることと、人間が見て動くことは別の測定）。**再測条件**: ①が緑になった後、
  同一状態で5セッション連続開始し、文言が他通知に埋もれず読める位置に出るかを記録する（実装と同日に測る）
- 出所: 2026-08-24〜25 実測。詳細は PJ memory `pitfall_shared_checkout_is_live_plugin`。
  **構造そのものの是正は `#548` が扱う。本 rule は緩和策**
