---
date: 2026-05-29
status: accepted
---
# pitfall-curate は PJ非依存の独立スキル（pitfall_manager とは別ライフサイクル）

## Context

figma-to-code で pitfalls.md を 200 件超まで磨く過程で、pitfall 運用に再現性のある「型」が確立した:
類似 pitfall の重複排除、普遍性分類（U/M/E + 汎用度）、三段階開示の配布版（top-N のみ agent に渡す）、
記録↔分類↔配布の同期ゲート。atlas-breeders など他PJでも pitfalls は貯まり、放置すれば同じ
重複・肥大化・配布漏れに必ず直面する。この型を「このPCの全PJで pitfalls を作るときに使える」
仕組みとして展開したい、というのが出発点。

調査の結果、2つの既存資産が判明した:
- rl-anything の `pitfall_manager`（scripts/lib/pitfall_manager/）は `is_self_evolved_skill()` で
  **自己進化スキルだけ**を対象にし、`skill_dir/references/pitfalls.md` を walk する密結合設計。
  任意PJの汎用 pitfalls.md には使えない。
- figma-to-code は独自 TS（`pitfall-similarity.ts` / `sync-pitfalls.ts`）で rl-anything と無関係に
  同種の機能を再実装していた（車輪の再発明）。

## Decision

figma の型を脱ドメイン化した **PJ非依存スキル `pitfall-curate`** を rl-anything に新設する。
器は新規共有CLIでなく rl-anything プラグインとした（plugin が既に全PJで有効、`similarity.py`
を再利用でき、将来 fleet/telemetry と接続できるため）。

1. **`pitfall_manager` とは統合せず別ライフサイクルで共存**。pitfall_manager は自己進化スキルの
   Candidate→New→Active→Graduated 状態機械に最適化されており、任意PJ汎用ツールとは関心が異なる。
   無理に統合すると双方の制約が絡む。
2. **判断は agent、決定論処理は script** という責務分割。普遍性分類（`Transferability`:
   universal/project/instance + `Generality` 1-5）と reframing 文の生成は意味理解が要るため
   スキル本体（LLM）が担い、`scripts/pitfall_curate.py`（CLI）+ `core.py`（純粋関数: parse /
   `find_similar_pairs` / `set_classification` / `mark_superseded` / `select_distill` /
   `check_sync`）は LLM を一切呼ばない。これにより単体テストが LLM 非依存になる（no-llm-in-tests
   ルールを構造的に満たす）。
3. **脱figma語彙**: figma の U/M/E は Figma 実装基準に縛られるため、`Transferability` ×
   `Generality` という汎用語彙に置換。`instance`（特定実装1件専用）は配布版対象外とし、
   配布版に載っていれば降格漏れ（stale）として検出する。
4. **設定でドメイン固有を吸収**: pitfalls.md/配布版のパス・top-N の N・threshold・
   mandatory-generality を引数化し、分類の判断基準は対象PJの CLAUDE.md に委ねる。
5. **figma 既存 TS 運用の置換は当面しない**（併存）。移行は別タスク。

## Consequences

- 新依存ゼロ（既存 `similarity.py` の jaccard/tokenize を再利用、sklearn 非依存）。決定論コアは
  LLM mock 不要で 13 テストが緑。
- 任意PJで `/rl-anything:pitfall-curate <pitfalls.md>` を呼べば dedup→classify→distill→sync の
  型が使える。新規PJの pitfall 運用導入コストが下がる。
- pitfall ロジックが rl-anything 内で2系統（pitfall_manager / pitfall-curate）並立する。将来
  共通化の余地はあるが、現時点では関心分離を優先（統合は再検討トリガー: 両者の parser/分類が
  実運用で頻繁に二重メンテになったら）。
- file-size-budget 遵守のため core.py（385行）と CLI（150行）に分割済み。

## References

- 実装: `skills/pitfall-curate/SKILL.md`, `skills/pitfall-curate/scripts/{pitfall_curate.py,core.py}`
- 既存資産: `scripts/lib/pitfall_manager/`（自己進化専用）, `scripts/lib/similarity.py`（再利用元）
- 着想元: figma-to-code `scripts/pitfall-similarity.ts` / `scripts/sync-pitfalls.ts`
- 関連 ADR: [022 fleet 観測・介入](022-fleet-observation-plus-intervention.md)（全PJ展開の器の文脈）
