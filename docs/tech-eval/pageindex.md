# PageIndex (VectifyAI) 技術評価

- **評価日**: 2026-05-15
- **対象**: [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex)
- **結論**: **不採用**（規模・構造・コスト・既存実装のいずれの軸でもプラスにならない）
- **再評価トリガー**: `docs/decisions/` の ADR が 100 本を超えたら再検討（現在 24 本）

## PageIndex の実体

ベクトルレス推論ベース RAG。長大ドキュメントを階層的 ToC ツリーに事前ビルドし、クエリ時に LLM がツリーを reasoning で navigate する retrieval システム。

- **オフライン**: PDF/Markdown → ToC ツリー（node 単位に summary + page range）
- **オンライン**: LLM がツリーを reasoning で降りていく
- **デフォルト**: `max-tokens-per-node=20,000`、深さ 3-5 の探索で 1 クエリ 50-100K トークン級は容易に消費
- **想定用途**: 法律文書・技術仕様書など「長大かつ目次構造を持つドキュメントへの専門的問い合わせ」
- **コア主張**: 「ベクトル類似度では拾えない関連性を reasoning で拾う」（"similarity ≠ relevance"）。トークン削減が主目的ではない

紹介文の「トークンコスト削減」は誤読。inference 時はむしろコスト高。

## evolve-anything 側の retrieval 実態

| レイヤー | 現状実装 | 規模 | PageIndex 適合性 |
|---|---|---|---|
| skill 間類似度 | TF-IDF + cosine (`scripts/lib/similarity.py`) | スキル数十件 × 数百行 | ✗ pairwise 比較用途。PageIndex は単一ドキュメント内の retrieval ツール |
| corrections → skill マッチング | Jaccard (`scripts/lib/discover/enrich.py`) | corrections JSONL | ✗ 短文同士のマッチ。ツリーを掘る対象がない |
| ADR/spec 検索 | なし（必要時 grep + Read） | 24 ADR + 2 spec = 計 1191 行 | ✗ context window に丸ごと入る規模。索引する意味なし |
| transcript 横断検索 | なし | 9925 jsonl / 1.9GB | △ 規模は合うが**会話ログに ToC 構造がない**。前提条件を満たさない |
| CC のスキル選択 | CC 本体の description matching | — | ✗ プラグインから差し替え不能 |

## 不採用の決定的理由

1. **構造ミスマッチ**: PageIndex は「目次のある長文ドキュメント」前提。当 PJ で唯一スケールが合う transcript 群には構造がない。索引前に summarize/segmentation が別途必要で、それは別問題に化ける
2. **規模ミスマッチ**: ADR 24 本・SPEC 2 本で計 1191 行。Read で全部入る。索引のオーバーヘッドが本体より重い古典的アンチパターン
3. **コストプロファイル衝突**: `.claude/rules/llm-batch-guard.md` で「LLM バッチ処理は事前に件数とトークン見積もり提示」を自分で課している。1 クエリ 50-100K の reasoning chain は逆方向
4. **重複機能**: 「similarity ≠ relevance」の問題意識は constitutional eval（LLM judge）と evolve-scorer のドメイン軸で既に実装済み。哲学が重複
5. **依存追加**: trigger engine にツリー再ビルドの発火条件を足す必要があり、メンテ面積が増える

## 借りる価値のあるアイデア

採用はしないが、以下は将来の参考として記録:

- 「ドキュメント階層から summary tree を build → LLM が navigate」は ADR が 100 本超えたら有効化候補（現状 24 本）
- スキル間関係を Jaccard でなく LLM reasoning で判定する案は `evolve-skill` の merge 判断に部分応用できなくはない。ただし audit + reorganize のテスト負債を増やすので優先度低
