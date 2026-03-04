## Context

v0.15.1 で `duplicate_candidates` のスタブ問題（全 C(N,2) ペアを返す）は解消済み。しかし `reorganize.merge_groups` 経由のパスでは、hierarchical clustering（閾値: cosine distance 0.7 ≈ similarity 0.3）で形成された大規模クラスタから依然として C(N,2) ペアが展開される。

現状の `merge_duplicates()` は reorganize_merge_groups のスキルリストを無条件に全ペア展開し（prune.py L593-599）、ペア単位の類似度チェックを行っていない。

## Goals / Non-Goals

**Goals:**
- reorganize 由来の merge_groups からのペア展開時に、ペア単位の類似度フィルタを適用して偽陽性を排除する
- 大規模クラスタ（N >= 5）での計算量爆発を防ぐ
- 既存の merge 抑制機構（suppression）やテストとの互換性を維持する

**Non-Goals:**
- reorganize のクラスタリング閾値自体の変更（別の concern）
- duplicate_candidates パスの変更（v0.15.1 で解決済み）
- LLM ベースの責務分析（コストが高すぎる）

## Decisions

### D1: フィルタリングの実装箇所 — `merge_duplicates()` 内でペア展開後にフィルタ

**選択:** `merge_duplicates()` の reorganize_merge_groups ペア展開ループ内でフィルタリングを行う

**代替案:**
- A) reorganize 側で merge_groups 生成時にフィルタ → reorganize の責務が増えすぎる。merge_groups は「候補群」であり、精査は merge 側の責務
- B) 新しい中間モジュールを作成 → オーバーエンジニアリング

**理由:** merge_duplicates() は既に duplicate_candidates と reorganize_merge_groups の両方を統合する責務を持つ。ここでフィルタリングするのが最も自然で、変更箇所が最小。

### D2: フィルタリング手法 — TF-IDF コサイン類似度（既存エンジン再利用）

**選択:** `scripts/lib/similarity.py` の `compute_pairwise_similarity()` を再利用し、reorganize 由来ペアにもペア単位の類似度チェックを適用

**代替案:**
- A) Jaccard 係数のみ → TF-IDF より精度が低い（語彙の重みを考慮しない）
- B) TF-IDF + Jaccard のハイブリッド → 複雑度が増す割に改善が限定的

**理由:** `compute_pairwise_similarity()` は v0.15.1 で実証済み。同インターフェースを再利用し、閾値は D4 で別途決定する。

### D3: 大規模クラスタの処理 — 閾値ベースフィルタで十分

**選択:** クラスタサイズによる特別処理は行わず、ペア単位の類似度フィルタのみで対応

**代替案:**
- A) N >= 5 のクラスタは duplicate_candidates との交差のみ処理 → ロジックが複雑化。proposal では提案したが、D2 のペア単位フィルタで十分に偽陽性を排除できる
- B) クラスタサイズの上限設定 → 恣意的で、正当な大規模クラスタを見逃す

**理由:** Issue #4 の例では 7 スキルのクラスタから 21 ペアが生成され、妥当だったのは類似度 0.81 の 1 ペアのみ。ペア単位で 0.80 閾値を適用すれば、この 1 ペアだけが残る。シンプルかつ十分。

### D4: フィルタ閾値 — 0.60（reorganize 用）

**選択:** reorganize 由来ペアには `REORGANIZE_MERGE_SIMILARITY_THRESHOLD = 0.60` を適用

**理由:** duplicate_candidates の閾値 0.80 はそのまま維持。reorganize 由来ペアは「同じクラスタに属する」という前提があるため、やや低い閾値で十分に偽陽性を排除できる。0.60 は Issue #4 のケース（妥当ペア: 0.81、次点: 0.50 未満）で適切に分離できる値。

## Risks / Trade-offs

- **[Risk] 閾値 0.60 が環境によって不適切** → `evolve-state.json` で設定可能にし、`evolve-fitness` で調整可能にする。初期値は保守的（偽陽性を減らす方向）
- **[Risk] sklearn 未インストール環境でのフォールバック** → 既存の graceful degradation に従い、フィルタリングをスキップして従来通り全ペア展開する（安全側: 偽陽性は出るがマージ漏れはない）
- **[Trade-off] フィルタにより正当なマージ候補を見逃す可能性** → 閾値 0.60 は十分に低く、本当に統合すべきスキルは通常 0.70 以上の類似度を持つ。また duplicate_candidates パスは独立して動作するため、0.80 以上のペアは常にキャッチされる
