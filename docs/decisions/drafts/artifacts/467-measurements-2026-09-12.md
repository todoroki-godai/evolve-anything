# #467 §1.5 実測 artifact（2026-09-12・再計測）

2026-08-16 の実測から約4週間後の再計測。**rev5 が Q1（パイロット再選定）の前提にしていた
観測値が変わった**ため、rev6 を書く前に記録する。**本 artifact は観測の記録であり、
Q0〜Q5 の答えを含まない。**

- 取得日時: `2026-09-12T00:28:37Z` / 測定時 HEAD: `ae1225ae`（本 artifact を含む作業ブランチの中間 commit。
  計測スクリプトと被計測コードは origin/main `96281252` と同一で、差分は本 artifact 3ファイルのみ）
- スクリプト: `scripts/bench/measure_467_proposal_kinds.py`（#624（`1d8254e4`・2026-09-05）で
  `recommended_artifacts_covered` が加わった以外は 2026-08-16 と同一）
- 再実行コマンド（**`--project-root` が必須**。省略すると worktree 起点になり、
  transcript ディレクトリが無いため §1.5.3 が全種 0 になる）:

```
python3 scripts/bench/measure_467_proposal_kinds.py \
  --project-root /Users/matsukaze-takashi/matsukaze-utils/evolve-anything \
  --output docs/decisions/drafts/artifacts/467-measurements-2026-09-12.json
```

- 安全性検証: `write_guard=passed` / `network_guard=passed` / `~/.claude/` マニフェスト差分 **0**
  （60,530 ファイル・narrow なし）。LLM 呼び出し 0。詳細は JSON の `safety_verification`
- **[実測] は本人の環境でのみ再現する**（等級の定義は draft §1.5.0）

## 母集団の違いに注意（本 artifact で新たに明示した点）

§1.5.1 と §1.5.3 は**数える母集団が違う**。同じ表に並べると齟齬を見落とす。

| 節 | 母集団 | 2026-09-12 の値 |
|---|---|---|
| §1.5.1 | **全PJ合算**（`corrections.jsonl` 全量） | 総数 323 / `last_skill` truthy 27 |
| §1.5.3 | **当PJ（`evolve-anything`）** | 同条件で corrections 75 / truthy **8** |

当PJの値は `runner._fetch_corrections_with_last_skill("evolve-anything")` を直接呼んで実測した
（2026-09-12 取得）。`corrections.jsonl` の `project` フィールドは 323 件すべて None で、
PJ の帰属は session 経由の推定に依存している。

## §1.5.1 観測値の変化（母集団: 全PJ合算）

| 観測 | 2026-08-16 | 2026-09-12 |
|---|---|---|
| `corrections.jsonl` 総数 | 175 | **323** |
| うち `last_skill` が truthy | 0 | **27** |
| 同一 session で correction の直前に Skill 呼び出しあり | 30 | **158** |
| そのうち SKILL.md を解決できたもの（**計測スクリプトの規則**） | 0 | **46** |
| `usage.jsonl` の Skill 呼び出し総数 | 888 | 1,000 |
| `source` 内訳 | backfill 8 / hook 2 / reflect_confirmed 165 | backfill 8 / **hook 57** / reflect_confirmed 258 |

`correction_type` は 4 種から 9 種に増えた（全量は JSON）。

**rev5 §1.5.1 の「`last_skill` は 0 / 172」は、この時点では成り立たない**（#478 の修正後）。

## §1.5.3 観測値の変化（母集団: 当PJ・対象 13 種）

| 種別 | 2026-08-16 | 2026-09-12 |
|---|---|---|
| `repeating_patterns` | 124 | **122** |
| `pitfall_candidates` | 0 | **7** |
| `rule_violation_observed` | 25 | **3** |
| `hook_candidates` | 0 | **1** |
| `recommended_artifacts` | 12 | **10**（内訳値 `recommended_artifacts_covered` 2） |
| `instruction_violation` | 0 | **0** |
| `missed_skill_opportunities` | 1 | 0 |
| `trajectory_skill_candidate` | 1 | 0 |
| `constraint_decay_findings` / `constraint_decay_warnings` / `stall_recovery_patterns` / `verification_needs` / `workflow_checkpoint_gaps` | 0 | 0 |

対象は 13 種（`recommended_artifacts_covered` は種別ではなく `recommended_artifacts` の内訳値）。
エラー 0。

## `instruction_violation` が 0 である件について（**この計測では解釈できない**）

`last_skill` の欠落が解消されたのに産出が 0 のままである。**ただしその理由を本計測から導くことはできない。**

- 計測スクリプト（`measure_467_proposal_kinds.py:444-449`）の SKILL.md 解決は**生名の glob**で、
  本番（`discover/runner.py:470-482`）の `resolve_plugin_skill_path` + `bare_skill_name` を
  再現していない（`plugin:skill` 形式を扱わない）。上表の「解決 46」はこの旧規則の値
- 計測は `query_corrections` の生値を使い、本番の `attach_last_skill` による read-time join を
  通していない
- 当PJの truthy 8 件の `last_skill` 内訳（2026-09-12 実測）: `readable-report` 4 /
  `evolve-anything:report-feedback` 1 / `evolve-anything:docs-refresh` 1 /
  `skill-creator:skill-creator` 1 / `status-recap` 1。**`readable-report` は main で廃止済み**で
  `~/.claude/skills` に実体が無い

**rev6 で A 案（`instruction_violation` の復活）を採るか捨てるかを判断する前に、
本番経路（join → `resolve_plugin_skill_path`/`bare_skill_name` → `detect_instruction_violation`）で
分解した実測を取ること。** 本 artifact はその分解を含まない。

## Q1（パイロット再選定）に渡る観測値

rev5 の4案それぞれが前提にしていた数字の、2026-09-12 時点の値だけを置く。**評価・見込みは rev6 の仕事。**

| 案 | rev5 が前提にした数字 | 2026-09-12 の同じ数字 |
|---|---|---|
| A `instruction_violation` | 産出 0・先行 Skill 呼び出し 30 件全件 SKILL.md 解決不能 | 産出 **0**・当PJ truthy **8**（解決可否は上記のとおり未分解） |
| B `rule_violation_observed` | 25 件（束ねて y/n 1件） | **3** 件 |
| C 3種一斉 | 12〜25 件/日 | 素の合算で `repeating_patterns` 122 + `pitfall_candidates` 7 + `hook_candidates` 1（rev5 の 12〜25 は束ね後の値なので直接比較できない） |
| D scoped-C（rules / hook ファイルの create のみ） | §5 の一部が必要 | `hook_candidates` **1**（`pitfall_candidates` 7 は pitfalls.md への追記候補であり、D の定義に直接は入らない） |

## この artifact の限界

- **[実測] は他環境で再現しない**（本人のストアに依存）
- 実ストアは追記され続けるため総数は取得時刻で増える。**2026-09-12T00:28:37Z の断面**
- 産出件数は各生成関数を個別 import して直接呼んだ値であり、`run_discover()` 全体を通した値ではない
  （朝の y/n に実際に何件出るかとは別物）
- 上記のとおり `instruction_violation` の経路は本番と乖離している
