---
date: 2026-06-02
status: accepted
---
# Belief Entropy / self-trained verifier は論文どおりでなく、決定論プロキシ + 既存ループ再利用で実装する

## Context

AI 研究トレンド 2 件を tech-eval で評価し、採用推奨「中」以上の 2 概念を rl-anything に取り込むことにした（[#285](../../CHANGELOG.md) / [#286](../../CHANGELOG.md)）。

- **#285 Belief Entropy（arXiv:2605.30159）**: LLM 生成の記憶要約が元情報に対してどれだけ不確実（hallucination / 欠落）かを測り、低品質な記憶を弾く。論文は内部状態のエントロピー推定（複数サンプリング / log-prob 解析）を前提とする。
- **#286 Self-Trained Verification（arXiv:2605.30290）**: 受理/棄却の判定器（verifier model）を自己生成データで継続学習し、生成物の採否を自律改善する。論文は専用モデルの追加学習を前提とする。

どちらも「論文の額面どおり」に実装すると rl-anything のアーキテクチャと衝突する:

1. **#285 を LLM ベースで実装する衝突** — belief 評価が走る場所は `auto_memory_runner`（Stop hook、毎ターン発火する hot hook）。ここに追加の LLM 呼び出しやサンプリングを差すと、`[hot hook eager import pitfall]`（毎発火 hook の重い処理がレイテンシを蝕む、duckdb eager import で 114ms→73ms を経験）と同型のコストを記憶生成のたびに払う。記憶生成は既に LLM 1 call を消費しており、その品質ゲートにもう 1 call を重ねるのは hot path で過大。
2. **#286 を verifier model 学習で実装する衝突** — rl-anything は「LLM 1 パス直接パッチ + 決定論ゲート」を設計の柱とし（[ADR-003](003-direct-patch-over-genetic-algorithm.md)）、モデル学習基盤（訓練ループ / 重み管理 / GPU）を持たない。専用 verifier の追加学習は PJ の前提（プラグイン・ローカル・決定論）と相容れない。

## Decision

論文のコア意図（記憶品質ゲート / 受理判定の自己改善）は採るが、実装は rl-anything の制約（hot-hook 原則・LLM ゼロの決定論・既存 recurring ループ）に合わせて翻案する。

1. **#285 → 決定論 retention/drift プロキシ**: `scripts/lib/belief_entropy.py` を新設。生成要約 vs 元 corrections のトークン集合演算で `retention = |src∩sum|/|src|`（情報保持率 = recall 近似）と `drift = |sum\src|/|sum|`（非接地率 = 1 - precision 近似）を計算し、`retention < 0.25 ∨ drift > 0.85` で `should_store=False`（書込前に破棄）。**LLM 呼び出しゼロ**。`similarity.jaccard_coefficient` のトークン化を再利用する。粗いトークン化（日本語等）で信号が乏しい場合は `low_signal` でブロックしない（安全側）。要約は frontmatter を剥がして body のみ評価する（構造トークンによる drift 過大評価を回避）。これは論文の厳密な不確実性推定ではなく、それに着想を得た**保守的な決定論プロキシ**（過剰ブロックを避け「明確な欠落・幻覚」のみ弾く）。

2. **#286 → 既存 evolve-fitness の相関分析を recurring ループで再利用**: 新規 verifier を学習せず、`fitness_evolution.detect_drifted_funcs(history)` が optimize/evolve の accept/reject 履歴から fitness 評価関数ごとの score-acceptance 相関を計算し、`CORRELATION_THRESHOLD`(0.50) を割った関数を「再 calibration 推奨」として検出する。これを audit の `build_calibration_drift_section`（observability）と trigger_engine の `_detect_calibration_drift`（session 終了時に `MIN_DATA_COUNT`(30) 以上 ∧ drift で `/rl-anything:evolve-fitness` を proactive 提案）の**共有単一ソース**にする。全 fitness 変更は**人間承認 MUST**で、本機構は advisory のみ（自動適用しない）。

3. **両者を observability contract に乗せる**: `belief_blocks` / `calibration_drift` を [ADR-028](028-observability-contract-audit-evolve.md) の `_OBSERVABILITY_BUILDERS` に登録し、markdown / 構造化の両経路へ自動伝播させる。データ駆動の適用判定（ログ/履歴が無い PJ は `None`＝対象外、稼働済みで該当なしでも `✓` を1行＝`silence ≠ evaluated`）。

## Alternatives considered

- **#285 を LLM judge で実装**（要約 vs source を LLM に「忠実か」判定させる）: hot hook に LLM 1 call 追加。記憶生成のたびにレイテンシ + トークンを払い、hot-hook eager import pitfall と同型。却下。決定論プロキシで「明確な欠落・幻覚」は十分捕捉でき、過剰ブロックも避けられる。
- **#285 を論文どおりのエントロピー推定**（複数サンプリング / log-prob）: プラグインから生成モデルの内部状態 / 複数サンプルにアクセスする経路がなく、コストも hot path で過大。却下。
- **#286 を専用 verifier model 学習で実装**: 学習基盤なし、[ADR-003] の決定論方針と衝突。却下。
- **#286 を完全に見送る**: score-acceptance 相関の劣化（calibration drift）は実在の劣化モードで、検出すれば evolve-fitness の起動判断に直結する。既存 `fitness_evolution` の相関分析が既にあり、recurring ループ（audit/trigger）に配線するだけで論文意図の大半を低コストで実現できるため、リフレームして採用。

## Consequences

- belief ゲートは hot hook 上で **LLM ゼロ・O(token)** で動く。記憶生成のレイテンシ増は無視できる（集合演算のみ）。閾値は保守的（retention 0.25 / drift 0.85）で、過剰ブロックより取りこぼし側に倒している = 「安全網」であって「厳格な品質判定器」ではない。閾値や評価式は将来の実データ次第で再 calibration し得る（覆されやすい判断）。
- `detect_drifted_funcs` を audit section と trigger_engine が共有する**単一ソース**にしたため、drift 判定ロジックが2箇所に分岐しない。fitness 変更は人間承認 MUST を維持（trigger は提案のみ）。
- 論文との差分を `belief_entropy.py` の docstring に明記（「Belief Entropy 論文の厳密な不確実性推定ではなく、hot-hook 原則に沿った LLM ゼロの決定論プロキシ」）。将来「やはり LLM judge が要る」と判断が覆る場合に備え、決定論を選んだ理由（hot-hook コスト）を残す。
- docs-platform 実機で E2E 確認: belief ゲートは実 corrections で「忠実=保存 / 無関係=block / frontmatter 剥離で drift 0.05→0.00」、observability は「対象外(None) → gate 発火・`belief_blocks.jsonl` 記録 → `⚠` surface」の遷移を実証（共有データ無汚染・一時 DATA_DIR で隔離）。
- この ADR は [ADR-003](003-direct-patch-over-genetic-algorithm.md)（LLM 1 パス + 決定論ゲート）と [ADR-028](028-observability-contract-audit-evolve.md)（observability contract）の上に立つ。「外部研究を額面でなく PJ 制約に翻案して採る」方針の記録。

## References

- 実装: `scripts/lib/belief_entropy.py`, `hooks/auto_memory_runner.py`, `scripts/lib/audit/sections.py`（belief_blocks / calibration_drift builder）, `scripts/lib/audit/observability.py`, `skills/evolve-fitness/scripts/fitness_evolution.py`（`detect_drifted_funcs`）, `scripts/lib/trigger_engine/session_corrections.py`
- テスト: `scripts/lib/tests/test_belief_entropy.py`, `hooks/tests/test_auto_memory_runner.py`, `scripts/tests/test_belief_blocks_section.py`, `scripts/tests/test_calibration_drift_section.py`, `scripts/tests/test_calibration_drift_trigger.py`
- 関連 ADR: [003 直接パッチ最適化](003-direct-patch-over-genetic-algorithm.md), [028 observability contract](028-observability-contract-audit-evolve.md)
- 出典: arXiv:2605.30159 (Belief Entropy), arXiv:2605.30290 (Self-Trained Verification)
- 学習: 外部研究は「論文どおり」でなく PJ 制約（hot-hook コスト・決定論・学習基盤なし）に翻案して採る。コア意図を抽出し、最も安い enforcement surface（既存 recurring ループ）に乗せる
