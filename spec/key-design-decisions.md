# Key Design Decisions

> このファイルは SPEC.md から分離された cold 詳細仕様です。
> 概要は [SPEC.md](../SPEC.md) を参照してください。

全24件。カテゴリ別要約は [architecture.md#key-design-decisions-カテゴリ別サマリ](architecture.md#key-design-decisions-カテゴリ別サマリ)、原文は [../docs/decisions/](../docs/decisions/) を参照。

## Frozen Executor + Trainable Curator（SkillOS 設計との同型性）

rl-anything は **Claude Code を frozen executor**、**plugin 層を trainable curator** として
分離する設計を採用する（[ADR-023](../docs/decisions/023-skillos-frozen-executor-trainable-curator.md)）。この設計は SkillOS 論文（Ouyang et al., 2026, arXiv:2605.06614）
が独立に実証した同型アーキテクチャと一致する。

SkillOS の報酬設計から取り込んだ要素:
- **r^comp**: skill 数 / invocation 数 による圧縮ペナルティ（skill バブル防止）
- **r^fc**: skill 別エラー率から推定する valid tool call 率

rl-anything の優位点（SkillOS 対比）:
- skill_triage の 5 択（SPLIT/MERGE を含む）vs SkillOS の 3 操作
- regression gate（`scripts/lib/regression_gate.py`）による safety 層

詳細: docs/research/skillos-tech-eval.md / [ADR-023](../docs/decisions/023-skillos-frozen-executor-trainable-curator.md)

## 4層メモリ結晶化（MemOS 対応設計）

rl-anything の corrections→evolve パイプラインは MemOS / HiMem（arXiv:2601.06377）の
L1→L4 結晶化アーキテクチャと同型の設計を採用する（[ADR-024](../docs/decisions/024-memory-crystallization-memos-correspondence.md)）。

| MemOS 層 | rl-anything 対応 |
|---------|-----------------|
| L1 トレース | `corrections.jsonl` / `sessions.jsonl` 等（Observe hooks が記録） |
| **Episodic 層** | `episodic.db`（DuckDB TTL 30d、`/reflect` approve で昇格。`episodic_store.py` / `episodic_retriever.py`）— L1 と L2 の橋渡し。クロスセッション短期記憶 |
| L2 ポリシー | `MEMORY.md` (auto-memory、`/reflect` で更新) |
| L3 ワールドモデル | `rules/*.md` + `CLAUDE.md`（`/evolve` で昇格） |
| L4 結晶化スキル | `.claude/skills/*.md`（`skill_triage` / `/evolve-skill` で生成） |

**ギャップマッピング（将来検討）**:

- **未実装: 層間矛盾検出** — L2（MEMORY.md）と L3（rules）の矛盾エントリを自動検出する仕組みがない
- **未実装: 自動 reconsolidation** — MemOS が定義する下向き伝播（上位層変更が下位層を更新）も未実装
- **未実装: ハイブリッド検索** — MEMORY.md は現状線形スキャン。MemOS/HiMem が提案する
  ベクトル検索 + 構造検索のハイブリッドは未実装
- **参照**: MemOS/HiMem (Zhang et al., 2026, arXiv:2601.06377)、[ADR-024](../docs/decisions/024-memory-crystallization-memos-correspondence.md)
