# ADR-031: accept/reject 履歴 (optimize history) を DATA_DIR の project スコープに集約する

Date: 2026-06-03
Status: Accepted
Related: #223（accept/reject 採点記録の導入）, #286（calibration drift surfacing）, [pitfall_global_datadir_single_file], [learning_install_is_not_enforcement]

## Context

fitness calibration（評価関数のスコアと人間の accept/reject の相関を測り、評価関数の劣化を検出する機能）の母集団となる `history.jsonl`（optimize/evolve-loop/evolve-diff の accept/reject 決定ログ）が、**読み書き3経路に分裂（split-brain）していた**。

| 主体 | 書き込み先 | readers から見えるか |
|------|-----------|---------------------|
| `optimize.py`（変種生成→承認/却下） | `<PLUGIN_ROOT>/skills/genetic-prompt-optimizer/scripts/generations/history.jsonl` | △ プラグイン更新でリセット |
| `run_loop.py`（evolve-loop） | `<cwd>/.evolve-loop/history.jsonl` | ❌ readers が読まない（孤立） |
| `record_evolve_diff_decision`（evolve diff 承認） | plugin generations（optimize と同じ） | △ 同上 |
| readers（`fitness_evolution` / `discover/errors` / `audit/aggregate_runs`） | plugin generations を読む | — |

複合する障害:

1. **更新リセット**: インストール版では readers が読むのは `~/.claude/plugins/cache/evolve-anything/evolve-anything/<version>/.../generations/history.jsonl`。バージョン更新で新 cache dir が seed（commit 済み 9 件）から始まり、累積が永久に頭打ちになる。
2. **run_loop の孤立**: evolve-loop の accept/reject は `cwd/.evolve-loop/` に落ち、calibration readers に到達しない。`aggregate_runs.py` も `RL_LOOP_DIR = Path.cwd()/".evolve-loop"` を別途参照しており、optimize 側 history と evolve-loop 側 history を別経路で集計しようとする構造分裂が残っていた。
3. **非永続**: errors/sessions/corrections は正しく永続 DATA_DIR（`~/.claude/evolve-anything/`）に置かれているのに、accept/reject 履歴だけプラグイン本体ディレクトリ内にあり設計から外れていた。

実測（全バージョン cache + dev ソース + 全 PJ の `.evolve-loop` = 31 ファイルを union+dedup）したところ、**ユニークレコードは 9 件、有効 decision（`human_accepted` 非 null）は 3 件のみ**。すなわち「これまで累積されていた」は幻で、split-brain と更新リセットにより**一度もまともに累積していなかった**。あるユーザー PJ（atlas-breeaders）は自前のレコード 0 件にもかかわらず、グローバル混在プール（evolve-anything 由来 2 件等）を読んで evolve レポートに「3/30」と表示し、他 PJ の数字を自 PJ のものと誤認させていた。これは [pitfall_global_datadir_single_file]（DATA_DIR は全 PJ 共通なので単一ファイルに PJ 固有状態を持つと別 PJ の状態が流用される）と同型である。

また「モジュールは存在するが配線先がバラバラで実質機能していない」点は [learning_install_is_not_enforcement] と同型の失敗である。

## Decision

1. **単一正準ストア `optimize_history_store.py` を DATA_DIR に新設する**。`token_usage_store.py` / `session_store.py` と同じ DATA_DIR 解決（`CLAUDE_PLUGIN_DATA` 優先、未設定時 `~/.claude/evolve-anything/`）を用いる。保存先は project スコープのサブファイル:
   ```
   DATA_DIR/optimize_history/<slug>.jsonl          # project ごとに分離
   DATA_DIR/optimize_history/_unattributed.jsonl   # slug 解決不能な target の保全先（calibration 母集団から除外）
   ```
   API: `append_entry(entry, slug)` / `load_history(slug)`。

2. **slug は worktree 安全に解決する**。`basename(git rev-parse --show-toplevel)` は worktree 内で worktree 名（例 `cause1`）を返し、本体 repo 名（`evolve-anything`）と食い違う。evolve は worktree で動くため、slug は **`basename(dirname(git rev-parse --git-common-dir))`** で解決し、worktree から記録しても本体 slug に正規化する。これを怠ると worktree ごとに別 slug へ散る二次 split-brain を生む。

3. **読み書き6箇所をすべて store 経由に差し替える**: `fitness_evolution`（reader + `record_evolve_diff_decision` writer）/ `discover/__init__.py` の `HISTORY_DIR` / `discover/errors.py` の `detect_rejection_patterns` / `optimize.py` の `save_history_entry`・`record_human_decision` / `run_loop.py`（split-brain 主原因）/ `audit/aggregate_runs.py`（`GENERATIONS_DIR/history.jsonl` 直読 + `RL_LOOP_DIR` 参照）。optimize の run 成果物（`generations/<run_id>/`）はエフェメラルな per-run 作業データなのでプラグイン内に残し、累積 SSoT である history.jsonl のみ分離する。

4. **calibration を project スコープに分離する（Option 2）**。reader は current PJ の slug のみ読む。「全 PJ 横断集計 + record に slug タグを付けて read 側 filter」というハイブリッド案は、read 側 filter の実装漏れで再汚染するリスクがあり [pitfall_global_datadir_single_file] を繰り返すため却下。PJ ごとのファイル分離の方が単純で安全。

5. **レガシーデータの migration は行わない**。救えるのは最大 9 件・有効 3 件で、`BOOTSTRAP_MIN=5` を下回り calibration を起動すらできない。target パスからの slug 逆引きは symlink/worktree/別 home で silent misrouting し得るうえ、migration コードは一度実行されたら削除されるべき使い捨てコードで保守コストが残る。**新規スタートとし、レガシーファイルは削除も参照もせず放置する**。「9 件を正しく分類する」より「今後正しく累積する仕組みを早く動かす」方が ROI が高い。

## Alternatives Considered

### 代替案A: 全 PJ 横断集計（slug なし単一ファイル）
現状の plugin generations と同じ「混ぜる」挙動を DATA_DIR で永続化するだけ。最小変更だが [pitfall_global_datadir_single_file] に抵触し、別 PJ の fitness func 適合度が混入する（atlas-breeaders の「3/30」誤表示そのもの）。却下。

### 代替案B: 全 PJ 集計 + slug タグ + read 側 filter（ハイブリッド）
1 ファイルに集約し各レコードへ `project_slug` を付与、reader が current PJ で filter。30 件閾値への到達は最速だが、read 側 filter を忘れた経路から再汚染する。傷跡ベースの pitfall を理論で上書きする形になり却下。

### 代替案C: レガシー migration を実装する
target→slug 逆引きで既存 9 件を各 PJ ファイルへ振り分ける。有効 3 件は `BOOTSTRAP_MIN` 未満で calibration を起動できず、逆引きの silent misrouting リスクと使い捨てコードの保守コストに見合わない。却下。

### 代替案D: 30 件ハードゲートを下げて少数データで回す
`fitness_evolution` には `<5=不足 / 5–30=簡易分析モード / ≥30=本分析` の段階が既にあるが、相関計算自体が `CORRELATION_WINDOW=20` を要求し、audit が消費する `detect_drifted_funcs` は 30 でハードゲートする。「5 件から動かせる」という見立ては別サブシステム（`pipeline_reflector/calibration.py` の `EWA α=min(N/30, 0.7)`、reflect の corrections 承認率校正）との混同であり、`fitness_evolution` には EWA alpha は無い。5 点の相関はノイズなので本 ADR の本筋（保存先 fix）とは分離し、audit surfacing の部分表示（20 件から）は任意の別変更として扱う。

## Consequences

- optimize/evolve-loop/evolve-diff のどの経路で記録しても、同一 project の `DATA_DIR/optimize_history/<slug>.jsonl` に集約され、`fitness_evolution` / `discover` / `audit` が同じファイルを読む。split-brain と更新リセットを解消。
- calibration は当該 project の accept/reject のみを母集団とする。atlas-breeaders は「他 PJ 由来の 3/30」でなく「自前の 0/30」を正直に表示するようになる（劣化ではなく誤認の是正）。
- PJ ごとに 30 件を貯める必要があり閾値到達は遅くなるが、これは calibration の正しさ（評価関数が当該 PJ で機能しているか）とのトレードオフとして受容する。到達速度の緩和（簡易分析モードの surfacing）は別 PR に切り出す。
- run_loop の accept/reject が初めて readers に到達する。`aggregate_runs.py` の二経路集計も store に一本化される。
- worktree から evolve/optimize を回しても本体 slug に正規化され、worktree ごとの slug 分散を防ぐ。
- レガシーの plugin generations / `.evolve-loop` の history.jsonl は残置されるが、誰も読まないため無害（次回のプラグイン更新で cache dir ごと自然消滅する）。
- 決定論・LLM 非依存を維持（保存・読み出し・slug 解決すべて）。
