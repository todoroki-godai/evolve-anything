# ADR-035: SkillOpt「スキルをプログラムとして訓練」を多世代 evolve-search で近似する

Date: 2026-06-04
Status: Accepted
Related: #305（調査ゲート / tech-eval）, [ADR-003]（direct-patch over GA）, #253（subgoal_scorer）, #256（evolution_operators）

## Context

Microsoft SkillOpt（daily report 2026-06-04, Rohan Paul 解説）は「agent skill は手書き・LLM 一発生成・緩い修正で劣化しやすい」と問題提起し、スキルを**小さな外部プログラムとして勾配的に訓練**すべきと主張する。

rl-anything の現状:
- optimize = LLM 1 パスパッチ + regression_gate（勾配的な「訓練」の反復ではない）
- `evolution_operators.evolve_generation` (#256) = BES 前向き進化探索だが **単一世代** のみ。crossover/mutate を 1 回かけて子を出すだけで「世代をまたぐ訓練の反復」が無い
- `subgoal_scorer.score_subgoals` (#253) = 候補を 5 サブゴールに分解する密な中間フィードバック（LLM 非依存・決定論）

#305 の受け入れ条件は「`rl-loop --evolve-search` を回す → **世代ごとの subgoal fitness が単調改善し、収束世代数が減る**」。現状の単一世代探索ではこの「世代ごとの単調改善」を観測できない。

論文の正式実装は未公開（調査ゲート）。フル準拠は不可能なため、**既存 BES の枠内で「勾配的訓練」を近似する自前実装**として進め、論文準拠への差し替えパスを残す。

## Decision

1. **多世代探索 `evolve_search` を `evolution_operators.py` に追加する**。`evolve_generation`（単一世代）をラップし、最大 N 世代まわす決定論関数。`fitness_fn: Callable[[str], float]` を**呼び出し側から注入**する（モジュール自身は LLM/subprocess を一切呼ばない＝no-llm-in-tests と再現性を維持）。

2. **subgoal fitness を勾配代理として使う**。「スキルを訓練対象とみなし勾配で最適化」の発想を、`subgoal_scorer` の total（0.0–1.0、LLM 非依存・決定論）を fitness 信号にすることで近似する。各世代でこの信号を評価し、進化演算子で集団を更新する。多世代まわしても LLM コストはゼロ円。

3. **エリート保存で best fitness の単調非減少を保証する**。各世代で「親集団 + 子集団」を fitness 降順に並べ上位を次世代へ引き継ぐ。これにより best fitness は世代をまたいで**単調非減少**になり（勾配上昇の近似）、#305 の「世代ごとに単調改善」を構造的に満たす。最終的な勝者の LLM 3 軸採点は **1 候補だけ**に限定する（コスト局所化）。

4. **patience による早期停止で「収束世代数を減らす」を満たす**。`patience` 世代連続で改善幅が `epsilon` 未満なら収束とみなし `generations` 前に停止する。`generations_run` / `converged` / `best_fitness_history` を返り値に含め、rl-loop が surface する（silence ≠ evaluated）。

5. **配線先は rl-loop `--evolve-search`**。`run_loop.py:_evolve_variants` を単一世代 `evolve_generation` から多世代 `evolve_search` に差し替える。fitness は既存の `run_subgoal_scoring`（#253 経由）を注入。手動 CLI 止まりにせず、既存フラグの挙動を多世代化する形で配線する。

## Alternatives Considered

### 代替案A: 論文コード公開を待ってフル実装する（現状維持 = 単一世代のまま）
#305 は調査ゲートだが受け入れ条件「世代ごとに単調改善」は単一世代では観測不能。論文公開時期が不明で、その間 evolve の収束改善が放置される。最小の足場（多世代 + エリート保存）は論文非依存で実装でき、公開後は fitness_fn を差し替えるだけで済むため、待たずに近似実装を入れる。

### 代替案B: fitness 信号に LLM 3 軸スコアラー（`_score_variant_axes`）を使う
真の品質信号に近いが、多世代 × 集団サイズ分だけ LLM を呼ぶことになりコスト爆発。no-llm-in-tests とも衝突する。subgoal fitness は決定論で安価かつ「frontmatter 保持 / trigger 網羅 / correction 反映 / line budget / slop」という訓練すべき軸を直接表すため勾配代理として妥当。最終勝者のみ LLM 採点にフォールバックする。

### 代替案C: 真の勾配（パラメータ微分）を実装する
スキルは離散テキストで微分不可。SkillOpt の「prompt as program」も実際は探索/RL 系であり、厳密な勾配ではない。エリート保存付き進化探索＋密な subgoal fitness が、テキスト空間での「勾配的訓練」の現実的な近似。

### 代替案D: ADR-003（direct-patch over GA）と矛盾するので進化探索を増やさない
ADR-003 は「日常運用のデフォルトは direct-patch」を定めたもので、`--evolve-search` は局所最適脱出のための**オプトイン**経路として #256 で既に共存している。本 ADR はその既存経路を単一世代→多世代に深めるだけで、デフォルト経路は変えない。矛盾しない。

## Consequences

- `evolve_search(candidates, fitness_fn, generations, offspring_count, patience, epsilon, rng)` が新公開 API。fitness_fn 注入により決定論・テストで LLM を呼ばない。
- `rl-loop --evolve-search` は最大 `EVOLVE_SEARCH_GENERATIONS`（=5）世代まわし、`best subgoal fitness 履歴` と収束有無を標準出力に surface する。エリート保存により best は単調非減少（#305 受け入れ条件①を構造的に保証）。patience（=2）で頭打ち時に早期停止（受け入れ条件②）。
- 進化フェーズの追加コストは subgoal fitness（決定論・LLM 非依存）なので**世代を増やしても LLM コストはゼロ**。LLM 採点は勝者 1 候補のみ。
- `_evolve_variants` の返り値が「子候補のリスト（複数）」から「勝者 1 件」に変わった。既存テスト（`test_loop.py`）を新挙動に合わせて更新済み。
- **論文準拠への移行パス**: SkillOpt の正式コードが公開されたら、`evolve_search` の `fitness_fn` を論文の訓練目的関数に差し替える／選択・変異演算子を論文準拠に置換する。多世代ループ・エリート保存・早期停止の骨格はそのまま再利用できる。本 ADR の Status を Superseded に更新し新 ADR で論文準拠版を記録する。
- **再評価条件**（#305 と同じ）: 論文コード公開後 / evolve の収束が頭打ち（同じ却下 type 反復）になったら本近似を見直す。
