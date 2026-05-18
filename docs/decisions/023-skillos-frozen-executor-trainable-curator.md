# ADR-023: SkillOS 論文に基づく Frozen Executor + Trainable Curator 設計の正当化

- **Status**: Accepted
- **Date**: 2026-05-19
- **Refs**: arXiv:2605.06614, Issue #69, #71

## Context

rl-anything は Claude Code（executor）を凍結した状態で、スキル/ルール層（curator）のみを
自律進化させるアーキテクチャを採用している。この設計判断に関して、外部の学術的根拠が
必要とされていた。

2026 年 5 月、Siru Ouyang ほか（Google DeepMind / UIUC）が発表した SkillOS 論文
（arXiv:2605.06614）が同型の設計を実証した。

## SkillOS の設計

- **Frozen Executor**: タスク実行 LLM（Qwen3-8B / Gemini-2.5-Pro）を凍結
- **Trainable Curator** (π_𝒮): 外部 SkillRepo（MD + YAML）を管理する独立ポリシー
- **SkillRepo**: Markdown + YAML frontmatter の skill 定義集（rl-anything の `.claude/skills/` と同形式）
- **Curator の訓練**: GRPO（DeepSeek-R1 系のグループ相対 advantage）で policy 学習

## rl-anything との対応関係

| SkillOS | rl-anything | 評価 |
|---------|-------------|------|
| Frozen executor (Qwen3/Gemini) | Claude Code（凍結） | 完全同型 |
| Trainable curator π_𝒮 | rl-anything plugin（進化対象） | 完全同型 |
| SkillRepo (MD + YAML) | `.claude/skills/` (MD + YAML) | 完全同型 |
| 3 操作 (insert/update/delete) | `skill_triage` 5択 (CREATE/UPDATE/SPLIT/MERGE/OK) | rl-anything 優位 |
| r^comp 圧縮ペナルティ | telemetry fitness 軸（本 ADR 実装）| rl-anything が採用 |
| r^fc valid call 率 | telemetry fitness 軸（本 ADR 実装）| rl-anything が採用 |
| GRPO policy 学習 | LLM 1-pass + regression gate | SkillOS 優位（研究規模）|
| Safety / rollback | `scripts/lib/regression_gate.py` | rl-anything 優位 |

## Decision

rl-anything の「Claude Code を frozen executor、plugin 層を trainable curator として分離する」
アーキテクチャは、SkillOS 論文が独立に実証した設計と同型であることを確認した。

この分離設計を **引き続き維持**し、SkillOS 論文を主要な設計根拠として ADR に記録する。
論文が実証した r^comp と r^fc の 2 reward 項を telemetry fitness に取り込む（Issue #67/#68）。

GRPO による curator policy 学習（強化学習）は現時点で採用しない。理由:
1. 単一 PJ での rollout データ量が不足（SkillOS は大規模タスクセット）
2. rl-anything の LLM 1-pass + regression gate が現在の規模に適切
3. CC が進化した場合は frozen executor の置き換えで対応可能

## Consequences

- r^comp / r^fc が telemetry fitness 5 軸に追加される（PR #67/#68）
- frozen executor + trainable curator の分離設計を SPEC.md に明示（本 ADR の引用）
- 将来 GRPO を検討する際の判断基準は「PJ 内の rollout 数 ≥ 1000」

## References

- Ouyang, S. et al. (2026). SkillOS: Learning Skill Curation for Self-Evolving Agents. arXiv:2605.06614
- ADR-002: Observe Hooks JSONL Architecture（observe.py = curator の観測層）
- ADR-005: Telemetry Score Architecture（r^comp / r^fc の追加先）
- tech-eval report: docs/research/skillos-tech-eval.md
