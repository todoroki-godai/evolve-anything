# HANDOVER — 2026-08-17（更新: セッション5）

## いまの状態（1行）

**#475 は設計レビュー通過（codex 4巡・tacchi 2巡・[Must] 0件）→ 実装フェーズ。**
設計 PR #482 / revert 側 PR #484 はマージ済み。残るは**反映側（A レーン）**と **#447**。

---

## 0. 完了したもの（再着手不要）

| 項目 | 結果 |
|---|---|
| PR #477（#467 rev5 設計差し戻し） | マージ済み |
| PR #481（#466 指摘率の分母を判定母集団に揃える） | マージ済み・CI 5本緑を実測 |
| #466 案C（未登録PJ6件を tracked に追加） | 完了。`~/.claude/evolve-anything/fleet-config.json` を頭が直接編集（bak あり）。tracked 18→24・ignored 5→4 |
| PR #483（#480 usage.jsonl の Skill/Agent 判別を単一ソース化） | マージ済み |
| **PR #482（#475 設計文書）** | **マージ済み**。`docs/decisions/drafts/475-adoption-to-rule-routing.md`（1180行）が正典 |
| **PR #484（#475 B レーン = revert 側）** | **マージ済み**（`105e16fe`）。CI 5本緑。worktree・ブランチとも撤去済み |
| weak signal 2件（提案種別を全て朝の y/n へ / フルスイートを頭で回さない） | どちらも昇格済み |

---

## 1. #475 の実装分割（設計 = `docs/decisions/drafts/475-adoption-to-rule-routing.md`）

**2レーンに分割し、触るファイルを完全に分離した。**

### B レーン（revert 側）— **完了・main 反映済み**

`_target.py` に scope `global_rule`(→`~/.claude/rules`) / `project_rule`(→`<repo>/.claude/rules`)、
`_availability._SUPPORTED_SCOPES` に同2種、`_apply.detect_subsequent_change`（read-only・
`_do()` の SHA256 conflict 判定を単一ソースのまま流用）、`evolve_revert_listing` の
`--list` 出力整形に後続変更検知。`store_registry` に `optimize_history/<slug>.jsonl` 登録 +
`shrink_freeze.FROZEN_STORES` 44→**45**。6方向 mutation すべて赤を実測。

### A レーン（reflect 側）— **実装中**（worktree `/Users/matsukaze-takashi/matsukaze-utils/wt-475a` / ブランチ `feat/475-reflect-lane`）

1. §6 状態分離: `reflect_status` に `promoted` 追加。`promote.py:371` の `applied` 直書きを `promoted` に
2. §6.1 実在確認ゲート: `update_reflect_status` に kw 専用 `target_path`/`draft_line`。不一致なら `apply_unverified`
3. §6.1 CLI: `evolve-reflect --apply <source_correction_id> --target-path <p> --draft-line-file <f>`（1呼出し=1件固定）
4. §6.2 正規化: `- ` 行が1行でもあれば箇条書きファイル / 0行なら素の文ファイル。未知の行頭記号は `apply_unverified`
5. §5.1 入力集合: `reflect.py:127-131`/`:1006`・`discover/suppression.py:196` を `in ("pending","promoted")` に
6. §5.1 既読化: `daily_review.py:111` の `record_reviewed(decision=...)`。「いまは反映しない」は `deferred`
7. §8.2 記録: 反映時に `append_history_entry_deduped` で scope `global_rule`/`project_rule` の entry を append（**B との契約**）
8. §4.6 移行バッチ: `reflect_confirmed` かつ `applied` の全件を `promoted` へ。既定 dry-run・`--apply` のみ実書込
9. §4.3〜§4.5 画面文言: `correction-review.md` Step 6.2 を固定4択に。`SKILL.md:255` の要約も
10. §6.1 迂回口: `skills/reflect/SKILL.md:83, 151-152` の「agent が JSONL 直接 Edit」を CLI 呼び出しへ

**8方向 mutation を委譲プロンプトで列挙済み**（実在確認 True/False 固定・`promote.py` 直書き復活・
入力集合の巻き戻し・`record_reviewed` 除去・`suppression.py` 巻き戻し・移行 no-op・素の文判定の除去）。

### 頭が自分でやる工程（ワーカーにやらせない）

**§4.6 の移行バッチの実データ実行**。`reflect_confirmed` かつ `applied` の167件を `promoted` へ戻す。
ワーカーは実 `~/.claude/` へ書けないので、スクリプトとテストだけ作らせ、**中身を確認してから頭が実行**する。

---

## 2. 並行中: #447（日本語トークナイズ）

worktree `/Users/matsukaze-takashi/matsukaze-utils/wt-447` / ブランチ `fix/447-ja-tokenize`。
`scripts/lib/similarity.py:186` の `tokenize`。`episodic_retriever.py:31` に別実装があるので単一ソース化を検討させている。

**委譲時の制約**: 外部形態素解析器（MeCab/janome/sudachipy）を入れない（CI が 3.11〜3.14 で回る）／
呼び出し元ファイルは編集しない（波及先が非常に多い）／期待値の書き換えは1件ずつ理由を報告／
**分割しすぎ**を検出する negative ケース必須／実 corrections での前後クラスタ数の実測必須。

---

## 3. #466 の実測結論（前言撤回を含む・変更なし）

`FREEZE_DELAY_DAYS = 3`。週 W の締切 = 週末 + 3日。

| 週 | 判定率 | 状態 |
|---|---|---|
| 2026-W32（8/3〜9） | 15.3%（129/842） | 締切 8/13 経過・**確定・修復不能** |
| 2026-W33（8/10〜16） | 100%（589/589） | 締切 8/20 に確定予定 |

表示条件は「100% の週が4週連続」→ W33 起点で W36（締切 **2026-09-09**）が初表示。
`judged_at` 欠落 3204件は**無関係**（補って再計算しても全週で変化ゼロ）。issue 化しない。

### ユーザー判断待ち（未回答）

**朝の自動処理が1日でも止まると、その週は永久に不合格になり4週連続が振り出しに戻る。**
推奨 =「止まったら気づける仕組みを1件だけ足す」。2026-08-17 に候補提示したがユーザーは #447 のみ選択。

---

## 4. 未着手の候補（ユーザーに提示済み・今回は選ばれなかった）

- **#478**: corrections の `last_skill` が全件 None で検出器2つが本番0件
- **#466 残件**: 朝の自動処理の停止検知
- **#465 / #469 / #470**: `beb49e76`（8/15・PR #474）で**すでに修正済み**なのに issue が open のまま。close 候補

---

## 継続中のユーザー指示（変更なし）

- 説明は**社長向け**（結論1行 → 詳細。実装名を地の文に出さない。判断を求めるときは推奨＋理由＋選ばなかった場合の3点セット）
- `scripts/bench/a0_eval_set.jsonl` は**再生成不能。消さない**
- commit / PR 本文に `Co-Authored-By` と close キーワードを書かない。issue 参照は素の `#N`
- push / PR は `gh auth switch --user todoroki-godai` と**同一 Bash 呼び出しで `&&` 連結**。済んだら `shohu` に戻す
- マージ前に head SHA の check-runs を実測して全緑確認
- 外部成果物に個人特定可能なローカルパスを書かない
- 並行 worker には `python3 -m pytest -n 0`（直列）を指示する
- worker は実 `~/.claude/` への書込み禁止（read-only の走査は可。DuckDB は `read_only=True`）
- **設計レビューは codex（正しさ）。利用者に見える面は tacchi も併走**

## 委譲プロンプトの標準項目（今回の反省を反映）

- **broadcast pkill 禁止を必ず書き写す**。2026-08-17 に B レーンのワーカーが
  `pkill -f "pytest -q -n 0"` を使いかけた（他ワーカーの引数順が違い実害ゼロ）。
  禁止自体は `worktree-parallel.md` に「委譲プロンプトに明記する」と書いてあるのに、
  **頭が書き写さなかった**のが原因。掃除は自分が起動した PID を保持して個別 kill。
- テストは `-n 0`（直列）。フルスイートは CI に回す（頭でも並行 worker でも回さない）
- 「タスク未完了時にテキストのみでターンを終えない」を必ず入れる（premature stop 対策）
- 報告フォーマットに終端マーカーを必ず入れる（再開判定のマーカーになる）

## 環境メモ

- メイン repo は `main`（`105e16fe`）
- worktree: `wt-475a`（A レーン）/ `wt-447`（#447）+ 別件の残骸2件（`wt-402b`、`.claude/worktrees/version-up`）
- **`codex exec` は `nohup sh -c "... > log 2>&1" &` で完全デタッチして起動する**
  （`run_in_background: true` だけでは2分でタイムアウト kill された実績あり）
- HANDOVER.md は untracked のまま維持（commit しない）
