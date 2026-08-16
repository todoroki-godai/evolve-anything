# #467 §1.5 実測 artifact（2026-08-16）

codex cold review 2巡目 [Must]C（`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md`
§1.5.0「[実測] の再現手順」に再現可能な実行スクリプトが無い）の解消として追加した実測 artifact。

- **取得日**: 2026-08-16
- **対象 commit**: `004fbd38ca5dd2276a6271048d13aa91e4c59650`（実行時点の HEAD。設計本文が前提とする
  `cfa77249` から進んでいるが、§3〜§9 の対象コードに変更は無い。差分は docs のみ）
- **入力パス**: `~/.claude/evolve-anything/`（`corrections.jsonl` / `usage.jsonl`。既定値のまま実行）
- **対象プロジェクト（§1.5.3）**: 本リポジトリ（`--project-root` 既定）
- **スクリプト**: `scripts/bench/measure_467_proposal_kinds.py`
- **実行コマンド**:

```
python3 scripts/bench/measure_467_proposal_kinds.py \
  --output docs/decisions/drafts/artifacts/467-measurements-2026-08-16.json
```

- **機械可読 JSON**: [`467-measurements-2026-08-16.json`](467-measurements-2026-08-16.json)
- **join ロジックの単体テスト**: `scripts/lib/tests/test_measure_467_join.py`
  （対象: `scripts/lib/measure_467_join.py`）

## §1.5.1 観測値（今回の実測）

| 観測 | 値 |
|---|---|
| `corrections.jsonl` 総数 | 172 |
| うち `last_skill` truthy | 0 |
| `source` 内訳 | `reflect_confirmed` 162 / `backfill` 8 / `hook` 2 |
| `correction_type` 内訳 | `semantic_idiom` 162 / `stop` 8 / `iya` 1 / `naoshite-request` 1 |
| `usage.jsonl` の Skill 呼び出し総数 | 888（後述の「台帳との差分」参照。Agent 呼び出しは除く） |
| correction と同セッションで先行する Skill 呼び出しがあるもの | 30 / 172 |
| その30件で SKILL.md が解決できる数 | 0 / 30 |

## §1.5.3 観測値（今回の実測・未接続13種の産出件数）

| 種別 | 産出件数 |
|---|---|
| `repeating_patterns` | 124 |
| `rule_violation_observed` | 25 |
| `recommended_artifacts` | 12 |
| `trajectory_skill_candidate` | 1 |
| `missed_skill_opportunities` | 1 |
| `pitfall_candidates` | 0 |
| `hook_candidates` | 0 |
| `instruction_violation` | 0 |
| `verification_needs` | 0 |
| `stall_recovery_patterns` | 0 |
| `workflow_checkpoint_gaps` | 0 |
| `constraint_decay_warnings` | 0 |
| `constraint_decay_findings` | 0 |

LLM 呼び出し: なし（全13種の生成関数・依存モジュールを 2026-08-16 に
`anthropic`/`openai`/`subprocess` grep で監査し不在を確認。
`critical_instruction_extractor.detect_instruction_violation` は docstring で
「LLM・subprocess を一切呼ばない」と明記されている経路のみを使用）。

## 台帳（2026-08-16 設計本文記載値）との差分

設計ドラフト §1.5.1/§1.5.3 に記載された値（対象 commit `cfa77249`）との比較。

| 観測 | 台帳値 | 今回の実測 | 差分の推定理由 |
|---|---|---|---|
| corrections 総数 | 171 | 172 | +1。実ストアは追記され続けるため（取得時刻がずれれば増える。差分は小さく無害） |
| `last_skill` truthy | 0 | 0 | 一致 |
| `source` 内訳 | reflect_confirmed 161 / backfill 8 / hook 2 | reflect_confirmed 162 / backfill 8 / hook 2 | 上記 +1 の内訳（`reflect_confirmed` に1件追加） |
| `correction_type` 内訳 | semantic_idiom 161 / stop 8 / iya 1 / naoshite-request 1 | semantic_idiom 162 / stop 8 / iya 1 / naoshite-request 1 | 同上 |
| `usage.jsonl` Skill 呼び出し | **5,574** | **888** | **要説明（下記）** |
| 先行 Skill 呼び出しあり | 28 / 171 | 30 / 172 | +2。実データの schema 調査で判明した「Skill 由来だが `ts` でなく `timestamp` キーを使う旧行」を index に含めるよう `measure_467_join.py` を修正した結果、正しく拾えるようになった件数が増えた（本実装が既存の join ロジックの正確性を上げた副産物。台帳側は+1の corrections 増分と整合しない +2 の増分だが、旧スキーマ行の拾い漏れ修正で説明がつく） |
| SKILL.md 解決できる数 | 0 / 28 | 0 / 30 | 「全件解決不能」という結論自体は不変 |
| 13種の産出件数 | 全て一致（124/25/12/1/1/残り8種0） | 全て一致 | 差分なし |

### `usage.jsonl` Skill 呼び出し総数の差分について（888 vs 5,574）

`~/.claude/evolve-anything/usage.jsonl` の実データを 2026-08-16 に schema 調査した結果、
このファイルは単一スキーマではないことが判明した:

| 分類 | 件数 | 判別方法 |
|---|---|---|
| Skill 呼び出し | 888 | `skill_name` を持ち `subagent_type` / `agent_id` を持たない |
| Agent 呼び出し | 4,651 | `subagent_type` / `agent_id` を持つ（`skill_name` は `Agent:<subagent_type>` 形式） |
| workflow-conformance 記録（別スキーマ） | 37 | `skill_name` でなく `skill` キーを使う。Skill/Agent ツール呼び出しとは別の記録経路 |
| 合計 | 5,576 | ファイル総行数 |

台帳の 5,574 はファイル総行数（5,576）にほぼ一致する（差は corrections と同様の追記分）。
これは **Agent 呼び出し（4,651件）を除外しない集計**だったと推定される。設計ドラフトの
本文は「`hooks/observe.py:84-87` は `tool_name == "Skill"` のとき... `usage.jsonl` は 5,574 件の
Skill 呼び出しを記録」と明記しており、意図は Skill 呼び出しのみのはずである。今回の実装では
`skill_name` の有無に加えて `subagent_type`/`agent_id` の有無で Agent 呼び出しを明示的に除外し、
かつ新旧スキーマ（`ts` キー / `timestamp` キー）の両方を Skill 呼び出しとして拾うようにした
（`scripts/lib/measure_467_join.py::is_skill_usage_record`）。この定義のほうが「Skill 呼び出し」
という語の意味に忠実だが、台帳の 5,574 とは一致しない。

**この差分は §1.5.1 の結論（「修理した場合の上限は少数」「SKILL.md 解決は 0 件」）を変えない。**
影響するのは「Skill 呼び出し総数」という分母の表示値のみで、「同一セッション内で correction に
先行する Skill 呼び出し」（30/172）と「SKILL.md 解決 0 件」という本節の核心の結論は今回の実測でも
維持されている。台帳側の 5,574 という値は本節の実装では再現できなかったため、正しいと確認できた
定義（Agent 呼び出しを除外した Skill 呼び出し数）をそのまま記録する。
