# ADR-050: daily-evolve — pull 型・学習素材ベースで全 PJ 横断 evolve 待ちを列挙

- Status: Accepted（Phase 1a/1b 実装完了 = #79/#80 / Phase 2/3 = #81/#82 は将来）
- Date: 2026-06-25
- Issue: #78（epic）, #79（Phase 1a: `fleet queue`）, #80（Phase 1b: launchd + 通知）, #81（Phase 2 将来）, #82（Phase 3 将来）
- Related: ADR-049（write barrier — 新ストアの書込ゲート）, ADR-031（PJ スコープ slug）,
  ADR-044（spec_trigger — main 着地検出）, ADR-037（claude -p 全廃 = LLM 消費口の集約）,
  `scripts/lib/fleet/queue.py` / `queue_state.py`, `scripts/lib/daily/`,
  learning: `learning_install_is_not_enforcement`,
  memory: `project_daily_evolve_design`（設計の生記録・撤回案A の実データ）

## 背景（動機）

全 PJ を 1 日 1 回 evolve で継続改善したい。各 PJ では普段それぞれセッションが回り、修正シグナル
（weak_signals / corrections）が蓄積する。これを取りこぼさず、**無人運用とユーザーの最終承認を両立**
させる運用が欲しい。素朴な実装（毎朝全 PJ を自動 evolve）は2つの壁に当たる:

1. evolve の適用判定は `AskUserQuestion`（人間承認）前提。cron で無人起動すると承認待ちでハングする。
2. 全 PJ 強制 evolve は LLM コストとノイズ（無意味な提案）を累積させる。

## 決定

**pull 型**にする。cron（macOS launchd）は「**取り込み + evolve 待ち PJ 一覧**」だけを決定論・ゼロ LLM
で作り、ユーザーが待ち PJ を対話セッションで上から処理する。

1. **無人で回せるのは決定論ゼロ LLM まで**。適用（evolve 本体）は必ず対話セッションで人間が承認する。
   → 自律進化を勝手に適用しない安全側の分業（ADR-037 の「LLM 消費は対話 /evolve に集約」と整合）。
2. **待ち判定 = 学習素材ベース**。`material_count = weak_unprocessed`（未昇格・未 expired の weak_signals）
   `+ new_corrections`（前回 evolve 以降の新規 corrections）が `--threshold`（既定 **5**・env
   `EVOLVE_QUEUE_THRESHOLD`）以上の PJ を待ちとする。補助シグナル（活動量）は列挙理由に併記のみ。
3. **per-PJ `last_evolve_at` state を新設**。既存 `evolve-state.json` はグローバルで PJ 別に
   「前回 evolve 以降」を測れないため、新ストア `evolve-queue-state.jsonl`（store_registry active・
   store_write barrier 経由 = ADR-049・`evolve --drain` の apply 境界でのみ書込・append-only +
   last-append-wins fold）を新設。state 不在 PJ は初回 = 全件待ち。
4. **Phase 分割**: 1a = `fleet queue`（判定ロジック・依存元）/ 1b = launchd + 通知（判定方式に**非依存** =
   queue を別プロセスのシェルコマンドとして叩くだけ）/ 2 = `evolve --dry-run` 提案バッチ（将来）/
   3 = 承認済み変更の worktree→PR 化（将来・マージは人間）。

## 不採用案

- **push 型（毎日全 PJ 強制 evolve）**: LLM コスト・ノイズ累積で却下。pull 型で「触る価値のある PJ」だけに絞る。
- **`corrections_unprocessed >= 閾値` で待ち判定**: 当初案。実データ検証（2026-06-24）で**全 PJ ゼロ＝機能しない**と
  判明し撤回。`processed` キーは corrections に存在せず（実体は `reflect_status`）、growth-state 11 PJ 全て
  `corrections_unprocessed=0`（pending は reflect が即時消費し滞留しない）、本番トリガー `evaluate_corrections` は
  `timestamp > last_run` のグローバル判定で `reflect_status` を見ない。[[pitfall_aggregate_without_decomposition]]
- **活動量ベース（subagents/sessions 件数）を主軸**: 「触った量」は改善余地を保証しない（大量に動いたが学ぶことが
  無い PJ もある）。主軸から外し補助併記のみ。
- **`evolve-state.json`（グローバル）流用**: PJ 別に「前回 evolve 以降」を測れず新ストア必須。
- **state を単一 dict 上書き JSON にする**: append-only `evolve-queue-state.jsonl` + read 側 last-append-wins fold に
  変更。この PJ の慣習（reward_ema / subagent_traces / correction_review_seen）に揃え、store_write barrier
  （ADR-049・atomic append primitive）に素直に乗せ、並行 evolve の write 競合を read-modify-write なしで吸収するため。

## Consequences

- Phase 1 で「朝に evolve 待ち一覧を出す」まで実現。実 E2E で実 DATA_DIR から `queue --json` が waiting 6 PJ を
  read-only 列挙し、#80 の通知 reader に通して systemMessage を生成することを実証（#79→#80 の契約接続）。
- **launchd 自動登録はユーザー判断**。`bin/evolve-daily-install` で任意に有効化。未登録でも手動運用は可能
  （`bin/evolve-fleet queue` 直叩き / `bin/evolve-daily-run` 単発実行）。
- 既存グローバル `evaluate_corrections`（reactive・単一 PJ ローカル・correction のみ）とは**補完関係**
  （queue は proactive・全 PJ 横断・weak+corr 合算の朝の入口）。競合せず役割が直交。
- Phase 2（#81 提案バッチ）/ Phase 3（#82 worktree→PR）は将来ラベルのまま未着手。閾値 5 は実 DATA_DIR
  dry-run で決定（active PJ 最小 5 vs trickle PJ 2 の自然 gap・ADR-044「閾値前に実データへ dry 適用」教訓に準拠）。
