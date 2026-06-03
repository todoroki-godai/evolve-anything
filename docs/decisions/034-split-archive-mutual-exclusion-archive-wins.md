# ADR-034: evolve の split↔archive 矛盾を本流で reconcile し archive を優先する

Date: 2026-06-04
Status: Accepted
Related: #301 #302（バグ報告）, [ADR-033]（evolve_introspect 自己解析）

## Context

evolve は reorganize フェーズで「SKILL.md が 300 行超のスキル」を `split_candidates`（分割候補）として、prune フェーズで「未使用スキル」を archive 候補（`zero_invocations` / `retirement_candidates` / `decay_candidates`）として独立に検出する。両者の間に相互排他チェックが無かったため、**大きくて未使用のスキル**（300 行超かつ zero invocations）が同じ evolve run で「分割せよ」と「淘汰せよ」の両方を受けるという矛盾が発生していた。

この矛盾は [ADR-033] の `evolve_introspect`（自己解析）が `self:split_archive_contradiction:<skill>` として正しく検出し、#301（`onboard-project`）・#302（`project-setup`）の2件の issue として半自動起票された。つまり「検出は機能していたが root cause が未修正」の状態だった。検出器が毎 evolve で同じ矛盾を再報告し続けるのは本質的な解決ではない。

## Decision

1. **reorganize と prune の両結果が揃った後、本流で相互排他を解消する**。`scripts/lib/evolve_introspect.py` に `reconcile_split_archive(result)` を実装し、`evolve.py` の prune フェーズ直後（Phase 4.1、self-analysis の前）に呼ぶ。決定論・LLM 非依存。

2. **archive を split に優先する**。同一スキルが分割候補かつアーカイブ候補のとき、そのスキルを `split_candidates`（および派生 `issues`）から除外する。理由: 同じ run で消そうとしている対象に分割投資するのは無意味であり、未使用（zero/retirement/decay）という強いシグナルの方が「これ以上構造を育てない」判断として優先されるべきだから。逆（split 優先）にすると、淘汰候補のスキルに分割という延命提案を出すことになり、prune の判断と矛盾する。

3. **除外は silent にせず記録する**。`reorganize.split_suppressed_by_archive` と `phases.split_archive_reconcile.suppressed` に除外スキル名を残し、evolve SKILL.md Step 4 が非空時に「分割候補から除外（archive 優先）: <skills>」を surface する（silence ≠ evaluated）。

4. **`evolve_introspect` の矛盾検出器は regression guard として残す**。reconcile が本流で先に矛盾を消すため通常は 0 件になるが、reconcile を通らない経路（reconcile 自体のバグ・将来の新フェーズ経路）で矛盾が再発したら検出器が surface する。検出器と reconcile は archive 判定の constant（`_PRUNE_ARCHIVE_KEYS`）とヘルパー（`_collect_archive_skills` / `_skill_name`）を共有し、policy を単一ソース化する。

## Alternatives Considered

### 代替案A: split を archive に優先する
未使用でも大きいスキルはまず分割し、archive はしない。しかし「未使用」は prune の強いシグナルであり、使われていないスキルを分割しても誰も使わない断片が増えるだけ。延命バイアスになり prune の意図に反するため却下。

### 代替案B: reorganize 側で split 検出時に usage を見て抑止する
reorganize は prune より前に走り、archive 判定（contribution score 等）をまだ持たない。reorganize に usage 集計を二重実装すると prune と判定がずれるリスクがある。両フェーズの結果が揃った後に reconcile する方が、archive 判定の単一ソースを保てるため却下。

### 代替案C: reconcile せず introspect の issue 起票に委ねる（現状維持）
検出器が毎 evolve で同じ矛盾を再報告し続けるだけで root cause は残る。#301 #302 が求めているのは「相互排他チェックの追加」であり、検出の継続ではない。却下。

## Consequences

- 大きくて未使用のスキルは archive 候補としてのみ提案され、分割候補からは自動除外される。#301 #302 の root cause が解消し、`evolve_introspect` の `split_archive_contradiction` は通常 0 件（✓）になる。
- 除外内容は記録・surface されるため、ユーザーは「なぜ分割提案が出なかったか」を追える。
- reconcile は `reorganize.split_candidates`（list of dict with `skill_name`）と prune の archive 系キーの出力契約に依存する。これらが変わるとテスト（`test_evolve_introspect.py` の reconcile 系）が回帰検出する。
- archive 判定の policy（どのキーを archive 寄りとするか）は `_PRUNE_ARCHIVE_KEYS` 単一ソース。検出器と reconcile が共有するため、片方だけ判定がずれることはない。
