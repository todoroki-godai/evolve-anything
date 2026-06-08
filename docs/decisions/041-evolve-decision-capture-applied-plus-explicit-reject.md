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
