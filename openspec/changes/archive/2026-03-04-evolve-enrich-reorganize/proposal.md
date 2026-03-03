## Why

evolve スキルは現在「新規スキル候補の発見（Discover）」と「未使用スキルの除去（Prune）」に特化しており、既存スキルを**育てる**ステップが欠落している。Discover が検出した error/rejection パターンは新規候補としてしか提案されず、関連する既存スキルの改善に活かされていない。また Audit が重複を検出しても「どちらかをアーカイブ」する二択しかなく、2つのスキルの良い部分を統合するロジックがない。スキル数が増えるにつれ、関連スキルの分散や肥大化も課題になっている。

## What Changes

- **Enrich Phase 追加**: Discover の error/rejection パターンを既存スキルと照合し、関連スキルへの改善提案（diff）を生成する新フェーズ
- **Merge サブステップ追加**: Prune Phase 内で duplicate_candidates に対して LLM ベースの統合版生成 → ユーザー承認 → 片方アーカイブの流れを追加
- **Reorganize Phase 追加**: 全スキルをクラスタ分析し、「統合すべきグループ」「分割すべき肥大スキル」を提案する新フェーズ
- evolve.py のフェーズ構成を拡張: Discover → **Enrich** → Optimize → **Reorganize** → Prune → Fitness Evolution → Report

## Capabilities

### New Capabilities
- `enrich`: Discover のパターンデータを既存スキルに照合し、改善提案を生成する機能
- `merge`: 重複スキルを LLM で統合して1つにする機能
- `reorganize`: スキル群全体をクラスタ分析し、再編提案を生成する機能

### Modified Capabilities
- (なし — 既存スキルの requirements 変更はなく、evolve パイプラインへの Phase 追加のみ)

## Impact

- **変更対象ファイル**: `skills/evolve/scripts/evolve.py`, `skills/evolve/SKILL.md`, `skills/prune/scripts/prune.py`
- **新規ファイル**: `skills/enrich/scripts/enrich.py`, `skills/reorganize/scripts/reorganize.py`
- **依存**: Discover の出力構造（behavior_patterns, error_patterns, rejection_patterns）、Audit の重複検出結果（duplicate_candidates）
- **LLM 呼び出し増加**: Enrich（パターン→スキル照合 + diff 生成）、Merge（統合版生成）、Reorganize（クラスタ分析）で各1回以上の LLM 呼び出しが追加される
