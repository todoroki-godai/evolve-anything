# ADR-051: result 依存キャプチャ副作用の drain 移植（emit→drain 2相の値運搬拡張）

- Status: Accepted
- Date: 2026-07-06
- Related: [ADR-041](041-evolve-decision-capture-applied-plus-explicit-reject.md)（#402 emit→drain 2相・pending 決定のキャプチャ）/ [ADR-045](045-evolve-drain-enforcement-marker-and-sessionstart.md)（drain enforcement marker + SessionStart）/ #146 / #150 / #135

## Context

`phases_capture.run_capture_phases()` の `if not dry_run:` 配下には、evolve が result に書いたフェーズ値を state store に確定する副作用がある。標準運用フローは `evolve --dry-run`（分析）→ 人間確認 → `evolve --drain`（適用）で完結し、`run_evolve(dry_run=False)` に**到達しない**ため、これらのブロックは構造的に死蔵する（#135 で根因特定・#146）。

#150 で **result 非依存**の2項目（`session_store.ingest` / `clear_snooze`）を `cli.py` の drain 分岐へ機械移植済み。これらは DATA_DIR のストアを再計算/再取得するため result を必要とせず、drain で単純に再発火できた（weak_signals #484 / reward_ema #64 / queue_state #79 / subagent_traces #135 / last_run #135-136 と同型）。

残る3項目は **result 依存**で、この移植パターンがそのままでは使えない:

1. **calibration state**（`state["last_calibration_timestamp"]` / `calibration_history`）— `result["phases"]["self_evolution"]` の calibrations / proposals を読む
2. **tool_usage_snapshot**（`state["tool_usage_snapshot"]`）— `result["phases"]["discover"]["tool_usage_patterns"]` を集計
3. **growth crystallization emit** — `_emit_growth_crystallization(result, project_dir)` が result 全体を消費

drain は `run_evolve` を回さないので result を持たない。既存の `evolve --drain --result-json <path>` の口（`drain_pending` が `evolve_decisions.pending` の抽出にのみ使用）は存在するが、標準フロー（`evolve --drain`・marker 経由）では `--result-json` は渡されず、3項目に必要な `result["phases"]` は消費されていない。

## Decision

**dry-run が書いた result を drain が読み、result 依存3項目を drain の apply 境界で発火する**（emit→drain 2相の「値運搬」版）。

1. **emit**: dry-run は既に `--output "$OUT"`（MUST・SKILL.md）で full result JSON を `$OUT`（`/tmp/rl_evolve_<slug>.json`）に書く。追加変更なし。
2. **drain**: `cli.py` の drain 分岐で `args.result_json` が与えられ読めた場合、その result の `phases.self_evolution` → calibration state persist / `phases.discover.tool_usage_patterns` → tool_usage_snapshot persist / `_emit_growth_crystallization(result, project_dir)` を発火（既存 persist 群と同じ error-surface パターン・`summary` に結果を積む）。
3. **timestamp 意味論**: `last_calibration_timestamp` / snapshot timestamp は **drain 時刻**（apply 境界＝「成果確定」時点）を使う。calibrations / tool_usage の**中身**は dry-run result から取る。
4. **graceful degradation**: `--result-json` が無い / 読めない / phases 欠落の drain では**3項目のみ skip**し、result 非依存の persist 群は継続する。`summary` に skip 理由を surface（silence≠evaluated）。
5. **運用接続**: SKILL.md の drain 手順（Step 7.8）を `evolve --drain --result-json "$OUT"` 常時付与に更新。dry-run の `$OUT` を同一対話セッション内で drain に渡す（他 persist は result-json 有無に非依存で後方互換）。

## Alternatives

- **案B（drain で再計算）**: drain 時に self_evolution / discover フェーズを再実行して3項目を算出する。→ 却下。drain の「軽い apply 境界」思想を壊し、dry-run が人間に見せた分析値と drain の再計算値がズレる（人間が承認したのは dry-run が提示した値であるべき）。
- **案C（放置）**: 現状維持。→ 却下。calibration trend / tool usage trend / growth 結晶化が標準フローで永久に蓄積されない（#146 の実害が継続）。

## Consequences

- **+** calibration_history / tool_usage_snapshot / growth 結晶化が標準フロー（dry-run→drain）で蓄積再開する。self-evolution の較正トレンド・tool 使用トレンド・成長イベントが再び観測可能になる。
- **−** drain が `$OUT` の生存に依存する項目が増える。別セッションでの手動 drain（`$OUT` が /tmp から消えた後）や `--result-json` 省略時は3項目 skip（他 persist は無傷）。これは graceful degradation で受容し、SKILL.md で「直近 dry-run の `$OUT` を渡す」と明記する。
- **−** result JSON（数十〜数百 KB）を `drain_pending` と capture で二重 read する可能性。実装は cli.py で1回 read して両者で共有する / 二重 read を許容する（小コスト）を実装者判断に委ねる。
- **リスク**: 古い `$OUT` を渡すと stale な calibration/tool_usage を記録する。SKILL.md の手順で「直近の dry-run が書いた `$OUT`」を明示して緩和する。時刻は drain 時点なので蓄積順序は壊れない。

## 実装スコープ（#146 委譲用）

- `cli.py` drain 分岐に result-json read → 3項目発火ブロックを追加（既存 persist 群の末尾・同じ try/except error-surface）。
- `phases_capture.py` の該当ブロック（`if not dry_run:` 配下の calibration / tool_usage / growth）は `run_evolve(dry_run=False)` 直接実行時の互換で**残す**（session_store #150 と同じ判断・冪等 or state upsert なので二重実行無害。ただし calibration_history は append なので二重 append を避ける dedup を実装時に確認）。
- SKILL.md Step 7.8 の drain コマンドを `--result-json "$OUT"` 付きに更新 + 早見表に3項目の drain 書込を追記。
- TDD: `--result-json` 有りで3項目が state/growth に書かれる / 無しで graceful skip + 他 persist 継続 / dry-run 純度（drain 前の分析は書かない）を assert。
