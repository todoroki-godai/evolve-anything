# ADR-024: MemOS L1→L4 結晶化アーキテクチャと rl-anything 4層メモリ設計の対応関係

- **Status**: Accepted
- **Date**: 2026-05-19
- **Refs**: arXiv:2601.06377 (MemOS/HiMem), Issue #149

## Context

rl-anything は corrections.jsonl（観測）→ MEMORY.md（短期記憶）→ rules/CLAUDE.md（行動ポリシー）→ skills/（実行可能知識）という 4 層の情報蓄積パイプラインを持つ。この設計は経験的に形成されたものであり、外部の理論的根拠が明示されていなかった。

2026 年初頭に発表された MemOS / HiMem 論文（arXiv:2601.06377）は、LLM メモリを
L1（トレース）→ L2（ポリシー）→ L3（ワールドモデル）→ L4（結晶化スキル）の 4 層に
分類し、下位層から上位層への「結晶化（crystallization）」を自律エージェントの知識獲得
メカニズムとして定式化した。

rl-anything の設計がこの 4 層アーキテクチャと構造的に同型であることを確認し、
各層のライフサイクル・更新トリガー・廃棄条件を ADR として明文化する必要がある。

## MemOS 4層モデルと rl-anything の対応

| MemOS 層 | 役割 | rl-anything 対応 | 形式 |
|---------|------|-----------------|------|
| **L1 トレース** | 生の観測・経験ログ | `corrections.jsonl` / `sessions.jsonl` / `usage.jsonl` 等 | JSONL（追記のみ） |
| **L2 ポリシー** | セッション横断の短期記憶・行動指針の素案 | `MEMORY.md` (auto-memory) | Markdown（`/reflect` で更新） |
| **L3 ワールドモデル** | 安定した行動規範・ドメイン知識 | `rules/*.md` + `CLAUDE.md` | Markdown（低頻度更新） |
| **L4 結晶化スキル** | 再利用可能な実行可能知識 | `.claude/skills/*.md` / `SKILL.md` | Markdown + YAML frontmatter |

## 各層のライフサイクル定義

### L1 トレース（corrections.jsonl / sessions.jsonl）

| 項目 | 定義 |
|------|------|
| **生成トリガー** | Observe hooks（15個）がセッション中に自動記録 |
| **更新方式** | 追記のみ（immutable log）。上書き・修正なし |
| **廃棄条件** | `prune` スキルによる TTL 期限切れ（デフォルト 90 日）、または明示的なアーカイブ |
| **結晶化先** | L2（`/reflect` 起動時に corrections を MEMORY.md へ昇格） |

### L2 ポリシー（MEMORY.md）

| 項目 | 定義 |
|------|------|
| **生成トリガー** | `/reflect` スキルが corrections.jsonl を解析してエントリを追加 |
| **更新方式** | `/reflect` および `post_tool_use_memory.py` hook による追記・修正。`update_count` で劣化検出 |
| **廃棄条件** | `update_count` が閾値超過（`update_count_guard.py`）または `/reflect` で obsolete 判定されたエントリ |
| **結晶化先** | L3（繰り返し参照されたエントリが `/evolve` で rules に昇格） |

### L3 ワールドモデル（rules/*.md + CLAUDE.md）

| 項目 | 定義 |
|------|------|
| **生成トリガー** | `/evolve` が MEMORY.md の安定パターンを rules に昇格、または `/reflect` が直接 CLAUDE.md を更新 |
| **更新方式** | `skill_triage` / `evolve` パイプラインによる低頻度更新（セッションまたがりで安定したパターンのみ） |
| **廃棄条件** | `/prune` による低使用率ルール削除、または `/reorganize` による統合 |
| **結晶化先** | L4（ルールとして繰り返し参照される操作手順が skill に昇格） |

### L4 結晶化スキル（.claude/skills/*.md）

| 項目 | 定義 |
|------|------|
| **生成トリガー** | `skill_triage` の CREATE 判定、または `/evolve` が発見したパターンから `/evolve-skill` で生成 |
| **更新方式** | `UPDATE` 判定時の直接パッチ（LLM 1-pass + regression gate）、または `/evolve-skill` による自己進化パターン組み込み |
| **廃棄条件** | `DELETE` 判定（低 valid_call_rate / 高 compression_penalty）または `/prune` による明示的削除 |
| **結晶化先** | なし（L4 が最上位層。SkillOS 設計との同型性は ADR-023 参照） |

## 結晶化フロー

```
L1 (corrections.jsonl)
  → [/reflect]  → L2 (MEMORY.md)
  → [/evolve]   → L3 (rules/*.md / CLAUDE.md)
  → [/evolve-skill / skill_triage] → L4 (skills/*.md)
```

各ステップの昇格判断は LLM + regression gate が担い、人間は `AskUserQuestion` で
最終承認する（Trainable Curator パターン、[ADR-023](023-skillos-frozen-executor-trainable-curator.md) 参照）。

## Decision

rl-anything の 4 層メモリ設計を MemOS L1→L4 結晶化アーキテクチャと対応付け、
各層のライフサイクル・更新トリガー・廃棄条件を本 ADR で明文化する。

設計の同型性を確認したが、以下の **未実装ギャップ** が存在する（将来検討）:

1. **層間矛盾検出**: L2（MEMORY.md）と L3（rules）に矛盾するエントリが共存しても
   現時点では検出されない。fleet Phase 2 での名前衝突検出が部分的に対応予定。

2. **自動 reconsolidation**: MemOS が定義する「上位層の知識が下位層の古いエントリを
   更新する」下向き伝播は未実装。現状は一方向（L1→L4）のみ。

3. **ハイブリッド検索**: MEMORY.md は現状線形スキャン。MemOS/HiMem が提案する
   ベクトル検索 + 構造検索のハイブリッドは未実装。

## Consequences

- 各スキル（reflect / evolve / evolve-skill / prune）の役割を 4 層モデルで説明可能になる
- 新規スキル設計時の層の帰属を明確化できる（どの層を対象とするか）
- 上記 3 つの未実装ギャップが将来の roadmap 候補として記録される
- SPEC.md に MemOS ギャップマッピングセクションを追記（本 ADR と同期）

## References

- (Zhang et al., 2026). MemOS: An Operating System for Memory-Augmented Generation. arXiv:2601.06377
- ADR-023: SkillOS 設計との同型性（Frozen Executor + Trainable Curator）
- ADR-002: Observe Hooks JSONL Architecture（L1 トレース層の基盤）
- ADR-010: Auto-Evolve Trigger Engine（L1→L2 昇格のトリガー）
- ADR-016: Skill Self-Evolution Pattern（L4 結晶化スキルの自己進化）
