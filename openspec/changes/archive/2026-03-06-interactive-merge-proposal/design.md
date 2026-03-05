## Context

現在の merge フローは2段階の閾値で動作する:
1. `reorganize.py` が TF-IDF + 階層クラスタリングで `merge_groups` を検出
2. `prune.py` の `merge_duplicates()` が `filter_merge_group_pairs()` でペア単位の類似度フィルタ（閾値 0.60）を適用

閾値 0.60 未満のペアは `skipped_low_similarity` として自動除外されるが、0.40〜0.60 の範囲にはドメイン知識があれば統合妥当なペアが存在する。

## Goals / Non-Goals

**Goals:**
- reorganize 検出かつ merge 閾値未満（0.40〜0.60）のペアに対して対話的統合提案を行う
- 承認/却下のフローを既存の merge suppression 機構と統合する
- 既存の `proposed`（0.60+）フローに影響を与えない

**Non-Goals:**
- merge 閾値（0.60）自体の変更
- reorganize のクラスタリングアルゴリズムの変更
- `duplicate_candidates`（semantic_similarity_check 0.80+）側への interactive 追加

## Decisions

### Decision 1: 新 status `interactive_candidate` を導入

**選択**: `merge_proposals` の status に `interactive_candidate` を追加し、0.40〜0.60 のペアを区別する

**理由**: 既存の `proposed`（自動提案）と `skipped_low_similarity`（無視）の間に明確な中間層を設けることで、SKILL.md 側のハンドリングが容易になる。`proposed` のフローを変更せずに済む。

**代替案**: `skipped_low_similarity` のまま SKILL.md 側で類似度を見て対話提案 → status だけでは判別できず、prune.py の出力構造に類似度スコアの追加が必要になり複雑

### Decision 2: 下限閾値 0.40 を `evolve-state.json` で設定可能にする

**選択**: `interactive_merge_similarity_threshold`（デフォルト 0.40）を `evolve-state.json` に追加

**理由**: 0.40 未満は統計的にほぼ無関係なペアであり、対話提案しても承認率が極めて低い。しかしプロジェクトによって適切な閾値は異なるため設定可能にする。

### Decision 3: `filter_merge_group_pairs()` に interactive 範囲の返却を追加

**選択**: 既存の `filter_merge_group_pairs()` の返り値を拡張し、`(passed, interactive)` のタプルを返す

**理由**: 類似度計算は TF-IDF 行列構築が主コストであり、1回の計算で passed と interactive の両方を分類するのが効率的。

### Decision 4: 対話フローは SKILL.md（evolve）側で制御

**選択**: `interactive_candidate` の AskUserQuestion 呼び出しは prune.py ではなく evolve SKILL.md の Step 5 で行う

**理由**: 型A パターン（Python は JSON 出力のみ、LLM 操作は SKILL.md）を維持。prune.py は判定と分類まで、対話と統合版生成は Claude が担当。

## Risks / Trade-offs

- **[対話疲れ]** interactive_candidate が多すぎるとユーザーが全却下する → 下限閾値 0.40 と、1回の evolve あたり最大3件の提案上限で緩和
- **[閾値チューニング]** 0.40 が最適とは限らない → `evolve-state.json` で調整可能。evolve-fitness で accept/reject データから最適化も可能
- **[後方互換]** `filter_merge_group_pairs()` の返り値変更 → タプル返却に変更するため、既存の呼び出し元（`merge_duplicates()`）を同時に修正する必要あり
