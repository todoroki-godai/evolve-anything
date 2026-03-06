## Context

`/rl-anything:optimize` は `optimize.py` で最適化を実行後、`save_history_entry()` で `history.jsonl` にエントリを追記する。この時点で `human_accepted=None` として記録される。CLI には `--accept`/`--reject` フラグと `record_human_decision()` メソッドが実装済みだが、SKILL.md の Step 3 には「結果をユーザーに報告する」としか書かれておらず、accept/reject を確認するワークフローが欠落している。

## Goals / Non-Goals

**Goals:**
- optimize SKILL.md の Step 3 に accept/reject 確認フローを追加し、`history.jsonl` に `human_accepted` が記録されるようにする
- 既存の CLI 実装（`--accept`/`--reject`/`--reason`）をそのまま活用する

**Non-Goals:**
- `optimize.py` の Python コード変更
- `fitness_evolution.py` の変更
- accept/reject の自動判定

## Decisions

### Decision 1: SKILL.md のみの変更で完結させる

**選択**: SKILL.md のワークフロー記述のみを修正する
**理由**: CLI 側（`--accept`/`--reject`/`record_human_decision()`）は既に完全に実装されている。問題はスキルの手順書に accept/reject 確認ステップが欠落しているだけ。Python コードの変更は不要。

### Decision 2: AskUserQuestion で確認後に CLI で記録

**選択**: Step 3 で AskUserQuestion ツールを使い accept/reject を確認し、その結果を `optimize.py --accept` or `--reject --reason "..."` で記録する
**理由**: rl-anything の他のスキル（evolve, prune 等）と同じ対話パターンに合わせる。`allowed-tools` に `AskUserQuestion` の追加が必要。
**検討した代替案**: 結果を自動 accept し、ユーザーが不満な場合のみ `--reject` を別途実行する方式。しかしこの方式では reject のハードルが高くなり、`human_accepted: null` のまま残るエントリが減らない（根本原因が解消されない）ため不採用。

## Risks / Trade-offs

- [ユーザーが毎回判断を求められる] → 最適化結果の品質を高めるために必要なフィードバックであり、evolve-fitness が機能するための前提条件
- [reject 時の reason が空になる可能性] → `--reason` はオプションなので空でも記録される。rejection_reason の蓄積は望ましいが必須ではない
