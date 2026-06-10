# ADR-046: アウトカム指標 v1 — utilization 恒久0 修理 + 行動アウトカム3軸を advisory 並走後に重み昇格判断

- Status: Accepted
- Date: 2026-06-10
- Issue: #423
- Related: ADR-044（しきい値は実コーパス dry 適用前に確定しない）, ADR-028（observability contract 単一ソース）, ADR-031（worktree 安全 slug）, ADR-042（DATA_DIR 一元化）, learning_observability_quality_evidence_and_meaning, learning_gate_design_needs_real_corpus_dryrun, learning_dryrun_verification_blind_spot

## 背景（症状）

env_score が全 PJ で 0.6 前後・Lv.6-7 に頭打ち。実測で構造要因を 2 つ確認した。

1. **utilization=0.0 が構造的**: `scripts/rl/fitness/telemetry.py` の `_find_all_skills` は
   `project_dir/.claude/skills/` のみ走査していた。plugin レイアウト（リポジトリ直下
   `skills/`）の rl-anything 本体では 0 件 → telemetry の重み 25% が死に枠。本リポジトリ実測で
   skills 検出 0 → utilization 0.0 を確認。
2. **スコアの大半が「構造の綺麗さ」**: coherence / constitutional は入力 proxy であり、
   「環境が良くなればユーザーの手戻りが減る」という目的変数を直接測る軸が無い。

## 決定

### 1. utilization のスキル探索を audit 収集系に統一（即修理）

`_find_all_skills` を `audit.artifacts.find_project_skill_dirs` に委譲する。後者は
`.claude/skills/`（通常レイアウト）と `skills/`（plugin レイアウト）の両方を走査し、
#419 の収集除外（node_modules / dot-dir / `.archive` / `.gstack-backup`）を
`is_excluded_skill_path` の共有で自動適用する。本リポジトリ実測: 修理前 skills=0 / util=0.0 →
修理後 skills=21 / util≈0.54。他軸（effectiveness/implicit/compression/fc_validity）の計算式は
不変（スナップショット的回帰なし）。

### 2. 行動アウトカム3軸を advisory（表示のみ）で導入 — スコア重みには入れない

`scripts/lib/audit/outcome_metrics.py`（決定論・LLM 非依存）で既存ストアのみから算出:

| 軸 | 分母 | 分子 | ソース | 方向 |
|---|---|---|---|---|
| correction 再発率 | 窓内 distinct `correction_type` | うち 2 セッション以上で発生した type 数 | corrections.jsonl | 低いほど良い |
| 一発成功率 | 窓内セッション数 | `error_count == 0` のセッション数 | sessions.jsonl | 高いほど良い |
| rework 率(近似) | 窓内で 1 度でも編集したセッション | 検証ツールを介さない連続編集が閾値(=3)以上のセッション | sessions.jsonl `tool_sequence` | 低いほど良い |

`scripts/lib/audit/sections_outcome.py` を observability builder（ADR-028 の
`_OBSERVABILITY_BUILDERS` 単一ソース）に登録し、markdown 経路と構造化経路の双方へ自動伝播する。
各軸に evidence（件数・session_id 例・再発 type 例）を併記する
（learning_observability_quality_evidence_and_meaning 準拠）。データ不足の軸は沈黙でなく
「データ不足（reason / store）」を明示する。

### rework の近似限界（捏造しない）

既存ストアに **編集対象ファイルの ID が無い**（usage.jsonl は Skill/Agent しか記録せず
`tool_name` も持たない。sessions.jsonl の `tool_sequence` はツール名の順序のみでファイルパス無し）。
よって issue 原文の「同一ファイルの N ターン内再編集率」は厳密には算出不能。
`tool_sequence` 上の「検証ツールを介さない連続編集バースト」を proxy として採用し、
本 ADR に限界を明記する（近似 = "rework 率(近似)" とラベル表示）。真の file 単位 rework が
必要になった場合は file 変更を ID 付きで記録するストア新設（別 issue）が前提条件。

## 3. 重み昇格の判断基準

3軸は **2〜4 週 advisory 並走 → 分布実測 → 重み昇格判断** とする。advisory 段階で重みに
入れないのは、しきい値・重みは実コーパス dry 適用前には確定できないから（ADR-044 の学び:
spec_trigger は実 40 commit 素振りが設計を 3 回覆した）。dry-run 検証の盲点
（learning_dryrun_verification_blind_spot: apply 後にしか出ない効果は dry-run で観測不能）も
あるため、実運用ストアでの分布蓄積を必須にする。

昇格の分布条件（すべて満たして初めて environment fitness の重みへ繰り入れ検討）:

- **分散が十分**: 軸の値が全 PJ で同値でない（測定バグ・死に枠でないことの証拠。
  learning_measurement_layer_diagnosis: 全 PJ 同値 = 測定バグ強シグナル）
- **データ件数下限**: 当該軸の分母が下限（暫定 correction≥10 / sessions≥30）を満たす PJ が
  複数ある
- **方向の妥当性**: env 改善イベント（reflect/evolve 適用）の前後で軸が期待方向へ動く相関が
  実測で見える

昇格時は coherence / constitutional（入力 proxy）を pass/fail ゲートへ降格し、重みは
アウトカム軸へ寄せる将来案を検討する（構造の綺麗さは「最低ライン」、目的変数は手戻り、と
役割を分離する）。本 ADR ではゲート降格は決定せず、advisory 並走の実測を待つ。

## 配置

- 算出: `scripts/lib/audit/outcome_metrics.py`（DATA_DIR から jsonl を直接読む。テストは
  `monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp)` で module 属性を直接差し替え＝
  文字列ターゲット patch を避ける既知 pitfall 準拠）
- 表示: `scripts/lib/audit/sections_outcome.py` → `observability.py` の builder 登録
- utilization 修理: `scripts/lib/audit/artifacts.find_project_skill_dirs` +
  `scripts/rl/fitness/telemetry._find_all_skills` の委譲
