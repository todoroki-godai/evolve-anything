# Housekeeping ステージ詳細（Step 7 Global / 7.6 / 7.8 / 8）

SKILL.md 側には各 Step の見出し・要点・MUST の1行要約のみを残してある。ここは表示テンプレ・分岐条件・コードの正準。
Step 7 の Custom/Merge サブステップと Step 7.5（Pitfall 剪定）・Step 7.7（用語集 bootstrap）は既存の
[prune-merge.md](prune-merge.md) / [glossary-seed.md](glossary-seed.md) を参照。

## Step 7: Global Skills（件数1行 + グローバル文脈の audit へ誘導 — #525-3）

Usage Registry の cross-PJ 使用状況を確認し、既存の `safe_global_check` で処理する。
**表示は冗長にしない（MUST・#525-3）**: global 候補は PJ 単独 evolve では判断材料が不足する（他 PJ での使用状況を見ないと淘汰可否を決められない）ため、**全件を1件ずつ持ち回らない**。

- **件数1行に畳む**: 「Global Skills: 淘汰候補 {N} 件（cross-PJ 使用状況の確認が必要）」と1行だけ surface する（実測で 76 件規模になり、PJ レポートに全件展開すると本来の PJ スコープの提案を埋もれさせる）。
- **グローバル文脈の audit へ誘導**: 個別判断は PJ 横断で見られる `bin/evolve-fleet status` / グローバル audit に委ねる旨を1行添える（「全件と判断材料は `bin/evolve-fleet status` で確認」）。
- 0 件なら「Global Skills: 淘汰候補なし ✓」を1行残す（silence != evaluated）。

> 補足: PJスコープ evolve では prune producer 側で global 候補を件数サマリ `{"count": N, "pointer": "全件と判断材料は \`bin/evolve-fleet status\` で確認"}` に畳んでおり（#586）、`global_candidates` はフル配列でなくこの dict が入る。レポートは `global_candidates.count` をそのまま {N} に使う（個別 skill 名は持たない）。全件配列が必要な cross-PJ 走査では `run_prune(pj_scoped=False)` を使う。

## Step 7.6: 合理化防止テーブル

evolve.py の出力に含まれる `rationalization_table` フェーズ結果を確認する。
`pitfall_hygiene()` 内で `generate_rationalization_table()` が呼ばれ、corrections のスキップパターンをテレメトリと突合した結果が格納される。

- `rationalization_table` フェーズが存在しない場合: データ不足のためスキップ。「合理化防止テーブル: データ不足 — スキップ」と表示
- `rationalization_table` フェーズが存在する場合:
  - `table` の各エントリをテーブル形式で表示（MUST）:
    ```
    ### 合理化防止テーブル
    | 言い訳 | スキップ後エラー率 | サンプル数 |
    |--------|-------------------|-----------|
    | {excuse} | {outcome_error_rate}% | {sample_count} |
    ```
  - `outcome_error_rate` が `None` の場合は「N/A」と表示
  - `enriched_pitfalls` があれば「既存 pitfall にテレメトリデータをエンリッチ済み: {N}件」と表示

## Step 7.8: evolve 提案 accept/reject drain（決定論キャプチャ, #360-A [ADR-041]）

fitness calibration の母集団 `optimize_history` を**日次 evolve ループで育てる**ステップ。
run_evolve 末尾の `emit_decisions` が、スキル内容提案（discover の `matched_skills` +
skill_evolve の high/medium 適性提案）の `before_sha` をキュー `DATA_DIR/evolve_decisions/<slug>.jsonl`
にスナップショット済み（`result.evolve_decisions`）。ここで適用実績と明示却下を記録する。

> **なぜ drain が要るか**: 従来は Step 3 の inline python で assistant が手で
> `record_evolve_diff_decision` を呼ぶ MUST だったが、実行されず optimize_history が空のままだった
> （SKILL.md MUST ≠ 決定論強制 = `install ≠ enforcement` の細粒度版）。この drain を Step として
> 固定することで「記録ステップ未実行」を構造的に塞ぐ。

**accept = 明示 accept イベント AND 適用実績 / reject = 明示却下 / skip = 記録しない**
（ADR-041, C: ハイブリッド。#376 で是正）。Step 3 でユーザーが承認しスキルファイルを実際に
変更したもの（明示 accept AND 適用済み）が accept、ユーザーが「不要」と却下した提案 id が
reject、未変更かつ未却下（保留）は母集団に入れない。

> **#376 是正**: 初版は「ディスク sha が変わっていれば accept」だったが、evolve 提案の適用と
> 無関係な通常 commit（別作業でのファイル変更）でも対象ファイルの sha はたまたま変わりうる。
> SessionStart 自動 drain（#421・対話チャネルを持たない）がこの判定をトリガーし、無関係な
> commit を accept と誤記録する実測事故が発生した。是正後は「ディスク差分」に加えて
> `evolve --drain --accepted <id...>`（#444。CLI 経由で `drain_pending(accepted={id, ...})` へ渡る）で
> 渡す**明示的な decision イベント**の両方が揃わないと accept にならない（下記参照）。ディスク差分は
> 整合性ガードとして残る（明示 accept があっても未適用なら pending のまま）。

> **#400 バグ#1 根治**: 旧版は「`--dry-run` の場合は未記録でスキップ」していたが、evolve の
> 標準フローは `evolve --dry-run` で分析 → assistant が Step 3 で対話適用、である。この運用だと
> emit がキューを書かない（dry-run 契約）ため、accept が**永久に記録されず optimize_history が
> 空のまま**だった（fitness_evolution が `0/30` から動かない真因）。修正後は `--dry-run` 分析だった
> 場合でも、**Step 3 の適用が済んだら必ず** ingest を実行する。ingest には result 同梱の
> `result.evolve_decisions.pending`（before_sha 付き）を直接渡し、apply 後のディスク差分から
> accept を決定論で取る（キュー不要）。`--dry-run` はあくまで「分析パスが書き込まない」意味であり、
> その後の対話適用は実変更なので ingest は `dry_run=False` で記録する。

**実行タイミング**: Step 3 の承認・適用フロー完了後に、分析が `--dry-run` だったか否かに関わらず
必ず以下の**単一コマンド**を実行する（#402: inline python をやめ、drain は1コマンドに集約。
これにより「assistant が inline スクリプトを書き損ねる」失敗面を縮める）。**Step 1 の dry-run が
`--output "$OUT"` で書いた `$OUT` を `--result-json` に渡す**（#146/ADR-051: calibration state /
tool_usage_snapshot の result 依存2項目を drain の apply 境界で確定するため。growth crystallization
emit は #379 Step 4 で growth-journal harness ごと削除済み・元は3項目だった）:

```bash
# $OUT は Step 1 の dry-run が --output に書いた result JSON（/tmp/rl_evolve_<slug>.json）。
# Bash 呼び出しは別シェルなので $OUT/$PJ を再導出してから渡す（#525-3）。
PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス（env の PJ があれば優先・無ければ cwd。バッチ経路 #400 本体では
                     # 呼び出し側が PJ を env で渡すだけで対応できる）
OUT="$(evolve --project-dir "$PJ" --print-out-path)"
evolve --project-dir "$PJ" --drain --result-json "$OUT"
```

- `evolve --drain` は marker（`emit_decisions` が `--dry-run` でも記録した `before_sha` 付き
  pending）を読み、明示 decision イベント（下記の `--accepted`/`--rejected`）と突き合わせて
  optimize_history へ記録し、marker をクリアする。**tool 文脈（CLI）で走る**ため reader と同一
  DATA_DIR に書く＝hook/tool の DATA_DIR split（#358）を踏まない。**`--accepted`/`--rejected` を
  渡さない単一コマンドでは evolve_decisions の accept/reject は記録しない**（#376）— weak_signals /
  calibration state / tool_usage_snapshot / remediation marker / correction_semantic Phase C は
  この呼び出しだけで確定するが、fitness 母集団（optimize_history）への記録は下記の
  `--accepted`/`--rejected` 付き呼び出しが必要（#444）。
- **`--result-json "$OUT"` で result 依存2項目も同居確定（#146/ADR-051）**: 標準フローは
  `evolve --dry-run` 分析 → 対話適用 → drain で完結し `run_evolve(dry_run=False)` に到達しない
  ため、phases_capture の `if not dry_run:` 配下（calibration state / tool_usage_snapshot）が
  構造死蔵していた（較正・tool 使用トレンドが標準フローで永久に貯まらない #146 の実害）。
  dry-run が書いた `$OUT` を drain が読み値を運搬して apply 境界で確定する（emit→drain 2相の
  値運搬版）。時刻は drain 時刻・中身は result 由来。結果は drain サマリの
  `result_state_persisted`（calibration_written / tool_usage_written）で surface される
  （growth crystallization emit・`growth_crystallized` キーは #379 Step 4 で削除済み）。
  **`$OUT` を渡し忘れ / 消えた場合は2項目のみ graceful skip**
  （`{"skipped": "no_result_json"}` 等）し、他 persist は無傷で完走する。
- **承認して適用した提案がある場合、または明示却下がある場合**は同じ `evolve --drain` 呼び出しに
  `--accepted <id...>` / `--rejected <id> <理由>`（却下1件につき1回繰り返す）を**追加する**（MUST・
  #376/#444）— `--drain` CLI は `--accepted`/`--rejected` を渡さない限り accept/reject を一切
  記録しない。`id` は `result.evolve_decisions.pending[].id`（`skill_path` で Step 3 の対象提案と
  対応づける）。承認も却下も無ければこれらのフラグは不要（pending のまま次回に持ち越される）。
  **CLI は重複指定（`--accepted`/`--rejected` 間・各フラグ内）・未知 ID（現在 pending に存在しない
  ID）・理由なし reject（空/空白のみ）を明確なエラーで拒否し、drain_pending を一切呼ばずに中断する**
  （部分書込防止）。引数名は既存の `genetic-prompt-optimizer --accept`/`--reject`（直近結果を丸ごと
  受理/却下する単数フラグ）とは別物なので混同しない。
- **enforcement の保険（#402、#376 で範囲縮小）**: drain を忘れても、適用済みで未 drain な提案が
  あれば**次回 SessionStart で `restore_state` が `evolve --drain` を促すリマインド**を出す
  （`undrained_applied` が marker の before_sha と現ディスク sha を突合、store 非依存で #358 回避）。
  ただし SessionStart hook には対話チャネルが無く `--accepted`/`--rejected` を代弁できないため、
  **この保険はリマインド表示のみで optimize_history への記録は行わない**（#421 が導入した無人
  auto-accept は #376 で撤回。hook 側の呼び出しは `drain_pending(slug=..., history_file=...)` のみで
  decision 引数を一切渡さない — `scripts/lib/session_notify/collectors.py` 参照）。記録は Step 3 の
  対話 → Step 7.8 の明示 `--accepted`/`--rejected` 呼び出しでのみ成立する。

- 何も適用せず（純粋プレビュー）何も却下しなければ、全件が skip に落ち記録されない（self-correcting）
- accept/reject は `record_evolve_diff_decision` 経由で optimize_history（ADR-031）へ冪等記録され、
  fitness_evolution の相関母集団 / `check_calibration_regression` の入力になる
- **決定論 weak_signals の永続化も同居（#484）**: `evolve --drain` は同じ apply 境界で
  決定論3チャネル（manual_edit_after_ai / esc_interrupt / rephrase）+ permission_deny を
  `persist_weak_signals_drain` で weak_signals.jsonl へ永続化する。理由は #400 と同型の盲点:
  標準フローは `evolve --dry-run` 分析なので run_evolve 内の `run_batch(dry_run=True)` は
  #491 契約で常にゼロ書き込みになり、決定論チャネルが**実 PJ で一度も永続化されない**
  （llm_judge だけが Phase B/C の apply 側で書かれて存在していた）。検出は冪等（signal_key
  dedup）なので tool 文脈・非 dry-run の drain で書くのが正。結果は drain サマリの
  `weak_signals_persisted`（detected/written/skipped_dup）で surface される。
- 結果（accepted/rejected/skipped 件数）を Report に報告する。`accepted >= 1` なら
  「fitness 母集団に +N 件記録 ✓」と1行 surface する（silence != evaluated）

## Step 8: Fitness Evolution — 評価関数の改善チェック

evolve.py の出力に含まれる `fitness_evolution` フェーズを確認する。

- `status: "insufficient_data"` の場合（出力契約は **#559 で {verdict, one_liner, details} に圧縮済み**。
  従来の誤読防止注記 #400 バグ#5 / #525-1 / #526-4 / #528-1 / #479 はこの 1 本に統合した）:
  - **`result.phases.fitness_evolution.one_liner` を1行そのまま出す。これが結論**（MUST）。
    `verdict`（機械判定）と `one_liner`（1行サマリ）が top-level の結論で、`data_count`/件数・
    3段落の長文説明・`structural_reason`/`next_action` はすべて `details` 配下に隔離されている。
  - **`details.message`（長文）と件数（`N/30`）は既定で出さない**。structural ケース
    （`details.structural_reason == "skill_evolve_not_scored"`）では `data_count` が構造的に 0 固定に
    なりやすく、`0/30` 単独表示は「あと 30 件貯めれば判定できる」という蓄積前提の誤読を生む。
    ユーザーが理由を尋ねたときだけ `details.message` / `details.next_action` を開示する。
  - **誤読の本質**（開示時に添える1行）: 対象外なのは **calibration（accept/reject 蓄積による再調整）
    だけ**で、fitness 関数自体は evolve-optimize / evolve-loop-orchestrator 実行時の評価に使用中。
    「fitness は使わない設計」を fitness 関数全体の否定と読ませない（#525-1）。
  - **整合（#479）**: structural ケースでは observability の `calibration_drift` 行と Step 2 の
    `has_fitness` 表示も「提案が出て初めて母集団が貯まる＝calibration は構造的に対象外になり得る
    （評価利用は継続）」で揃え、`calibration_drift` を「あと N 件で判定可能」と蓄積前提で言い直さない。
- `status: "bootstrap"` の場合:
  - 「簡易分析モード (N/30件)」と表示
  - 基本統計（承認率、平均スコア、スコア分布）を表示
  - 相関分析は行わない旨を注記
- `status: "ready"` の場合:
  - score-acceptance 相関を表示（相関 < 0.50 なら警告）
  - 頻出 rejection_reason があれば新軸追加を提案
  - 提案がある場合、AskUserQuestion で承認を求める（MUST）
  - 承認されたもののみ fitness 関数に反映
