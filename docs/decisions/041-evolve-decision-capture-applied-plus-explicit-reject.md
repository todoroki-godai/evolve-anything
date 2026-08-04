# ADR-041: evolve 提案 accept/reject の決定論キャプチャ（適用実績 + 明示 reject）

- Status: Accepted
- Date: 2026-06-08
- Related: #360（調査）, #356（pairwise calibration / un-trippable ゲート）, ADR-031（optimize_history_store）, ADR-037（auto_memory broker emit→drain）, #223（evolve-diff 採点記録）

## Context

fitness calibration（`check_calibration_regression`）の母集団は accept/reject 履歴
（`optimize_history/<slug>.jsonl`, ADR-031）。#360 で「全 PJ で空」を観測した。

調査の結論（#360 の当初前提を訂正）:

- evolve には optimize_history への writer が**既にある** — `record_evolve_diff_decision`
  （`fitness_evolution.py`）が `_default_history_file()` = `optimize_history_store.history_path()`
  に書く。evolve SKILL.md Step 3（#223）がスキル diff の accept/reject 時に呼べと MUST 指示。
- だが本番では決定論コードから呼ばれず、**assistant が SKILL.md の MUST に従い手で python
  ブロックを実行する**ソフト強制。かつトリガーが `matched_skills` のスキル diff のみ。
- → 「記録ステップが実行されない」と空のまま。`install ≠ enforcement` の SKILL.md 版。

## Decision

accept/reject を**決定論的にキャプチャ**する。evolve SKILL.md 1 実行内で完結する
emit→（インライン適用）→drain の2相にし、accept はディスク差分から、reject は明示シグナルから取る:

- **accept = 適用実績**: `emit_decisions`（run_evolve 末尾）が候補スキルの `before_sha` を
  スナップショットしてキュー `DATA_DIR/evolve_decisions/<slug>.jsonl` に書く。`ingest_decisions`
  （Step 7.8 drain）が `after_sha != before_sha`（＝適用された）を accept として記録する。
  assistant の記録手作業に依存しないので、#360 の失敗モード（記録未実行）を構造的に塞ぐ。
- **reject = 明示シグナル**: ユーザーが「不要」と却下した提案 id のみ drain が拾い reject 記録。
- **skip = 記録しない**: 未変更かつ未却下（保留/後回し）は母集団に入れない。reject ノイズを防ぐ。

書き込みは既存 `record_evolve_diff_decision` を再利用（fitness_func=`skill_quality` で
after_content を採点 → optimize_history へ冪等記録）。母集団は「混合でなく増量」を保つ。

対象は (1) discover の `matched_skills`（skill diff, #223/Step 3 と同クラス）と
(2) skill_evolve の high/medium 適性 assessment（自己進化パターン組み込み提案）。
どちらも適用されれば SKILL.md content が変わるため fitness_func=`skill_quality` で均質に採点でき、
母集団が「混合でなく増量」になる。remediation の fix は target が rules/hooks/構造と異種で
skill_quality 母集団の均質性を壊すため**対象外**（意図的スコープ）。

## Why not 他案

- **A（明示シグナル emit→drain のみ）**: 人間の意図は正確だが drain 呼び出しが assistant 依存の
  まま＝#360 と同じソフト強制リスクを継承。
- **B（適用実績のみ）**: 完全決定論だが skip/保留も reject 扱いになり母集団が汚れる。
- **C（採用 = ハイブリッド）**: accept を決定論（堅牢）、reject を明示（意図正確）、skip 除外
  （ノイズ排除）で母集団が最もクリーン。

## Consequences

- evolve のたびに optimize_history が育ち、#356 の calibration ゲートが trippable になる。
- `--dry-run` は emit/ingest とも書き込まない（pitfall_dryrun_stateful_store_write 準拠）。
- emit はキューを毎 run 上書き（プロセス跨ぎの bridge は単一バッチ）。drain skip 時はその run の
  シグナルを放棄（次 emit で上書き）。within-run の正しさを優先。
- 新規ロジックは `scripts/lib/evolve_decisions.py` に隔離（evolve.py は budget 超過のため追記は
  emit 呼び出し1行のみ）。

## Amendment (2026-08-04, #376): accept = ハッシュ差分単独では成立しない

Decision の B案「accept = 適用実績（ディスク差分のみ）」を、B'案「accept = 適用実績 AND
明示的な decision イベント」へ是正する。

### 何が起きたか

`ingest_decisions`（`scripts/lib/evolve_decisions.py`）の accept 判定は
`after_sha != before_sha` の一致だけを見ており、**その diff が evolve 提案の承認によるものか、
無関係な通常 commit（別作業でのファイル変更）によるものかを区別していなかった**。

さらに #421 で追加した SessionStart 自動 drain（`restore_state._deliver_evolve_drain`）が、
対話チャネルを持たないままこの判定をトリガーしていた。結果として、SessionStart のたびに
「evolve 提案の対象ファイルがたまたま変わっていた」だけで自動的に accept が記録されるように
なり、2026-08-04 に通常の実装コミット（spec-keeper/evolve の SKILL.md 変更）が accept として
誤計上される実測事故が発生した（issue #376）。

### 是正後の Decision

```
emit
  └─ pending
       ├─ 明示的な accept イベント AND after_sha != before_sha → accepted
       ├─ 明示却下                                            → rejected
       └─ 証跡なし                                            → pending のまま
```

`before_sha`（ディスク差分）は**捨てず整合性ガードとして残す**（明示 accept はあっても
実際に適用されていなければ accept にしない）。ただし**単独では accept の十分条件にしない**。
「明示的な accept イベント」は、`ingest_decisions(accepted={id, ...})` /
`drain_pending(accepted={id, ...})` に proposal id の集合として渡す。この集合は評価詳細
プロトコル（AskUserQuestion の承認）から Step 7.8 の drain 呼び出しへ inline で渡される
人間の判断であり、SessionStart hook のような対話チャネルを持たない呼び出し元は空集合しか
渡せない（＝正しく「何も記録しない」side に倒れる）。

### 既存レコードの扱い

是正前に記録された accept（`record_evolve_diff_decision` 経由・`decision_source` を持たない
`source=evolve_remediation` レコード）は、削除でなく `fitness_eligible: false` で無効化し
`skill_quality` の母集団（`fitness_evolution.run_fitness_evolution`）から除外する
（`scripts/lib/legacy_accept_migration.py`、既定 dry-run）。

### 波及

- `evolve_decision_ids._proposal_id` を絶対パスから repo 相対パス + repo_id ベースへ変更
  （worktree ごとに同一提案が別 ID になり pending が worktree 数だけ residue する副次バグの
  同時是正）。
- `is_orphaned_worktree` で削除済み worktree の pending を orphan として queue から分離。
- 詳細は issue #376 参照。
