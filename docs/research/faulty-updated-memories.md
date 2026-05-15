# tech-eval: Useful Memories Become Faulty When Continuously Updated by LLMs (arXiv:2605.12978)

- **評価日**: 2026-05-15
- **対象**: Zhang et al., "Useful Memories Become Faulty When Continuously Updated by LLMs" (2026-05-13)
- **結論**: 🔴 **直接適用な警告、現運用に既に対応されているが追加 guard を検討する価値あり**
- **再評価トリガー**: 不要（既に取り込むべき教訓は確定）
- **実体**: エージェントが自身のメモリ（経験ログ・知識ベース）を LLM 自身に更新・要約させると、複数ラウンドの再要約で誤りが増幅することを実証

## 論文の核心主張

1. **オリジナル情報と LLM 要約版の乖離は時間で蓄積する**（再要約のたびに少しずつ歪む）
2. **誤りは指数的に増幅する**ことが複数ラウンドで観測される
3. **対策**: オリジナルデータの保持 / 再要約の回数制限 / human-in-the-loop

## rl-anything 側の現行実装と照合

| 論文の警告 | rl-anything 側の対応状態 | 評価 |
|-----------|--------------------------|------|
| LLM による MEMORY.md 自動更新 | `reflect` skill が corrections.jsonl から CLAUDE.md/rules に反映 | ⚠ リスク該当 |
| 元データの保持 | corrections.jsonl は append-only（要約せず保持） | ✅ 安全側 |
| 再要約の連鎖 | MEMORY.md エントリは `[[name]]` リンクで個別 MD に分割、要約せず追記 | ✅ 安全側 |
| memory_temporal の supersede 検出 | `is_superseded` で stale を検出 | ✅ |
| reflect_provenance | `test_reflect_provenance.py` で出所追跡をテスト | ✅ |
| 自動更新のレビュー gate | reflect は手動 trigger（auto は提案のみ） | ✅ |
| **再要約による情報減損 guard** | — | 🔶 未実装 |
| **memory 更新の round 数記録** | — | 🔶 未実装 |

## 採否理由

**既に効いている安全策**:
- corrections.jsonl は LLM で要約せず raw event を保持 → 元データ保持原則を満たす
- MEMORY.md は単独 MD ファイルへの追記運用で、再要約による減損は起きにくい
- `memory_temporal.py:is_superseded` で陳腐化検出済み
- reflect skill は最終反映前に人間レビューを通す現運用

**論文が新たに示唆する改善余地**:
1. **memory 更新の世代カウント**: 同じ memory が何回 LLM 経由で書き換えられたかを記録するメタデータ
2. **N 世代以上書き換えられた memory は元 corrections を再参照させる**強制 guard
3. **`reflect` 実行時に「過去 N 回 update された memory は要注意」warning を出す**

## 借りる価値のあるアイデア

| アイデア | 推奨度 | 適用先 |
|---------|--------|--------|
| memory frontmatter に `update_count` を追加 | 中 | `reflect_utils.py` の memory 更新パスに `update_count++` を入れる |
| `update_count >= 3` の memory は audit で warning 表示 | 中 | `scripts/lib/audit/issues.py` に新ルール追加 |
| `archive_change_history.md` 的な「元情報への lineage」を MEMORY.md エントリに必須化 | 低 | 現状でも `[[name]]` リンクで部分実装済み |

## 推奨アクション

| 概念 | 推奨度 | アクション | 再評価条件 |
|------|--------|------------|------------|
| corrections.jsonl 永続化 | 不要 | 既実装 | — |
| memory `update_count` 追加 | **中** | Issue 化推奨（reflect 安全性向上） | — |
| reflect 実行時の世代 warning | 中 | 上記 Issue に統合 | — |

## 関連

- 現行コード: `scripts/reflect_utils.py:302-454` (memory 読み書き), `scripts/lib/memory_temporal.py` (stale/superseded), `scripts/tests/test_reflect_provenance.py`
- 関連 ADR: `docs/decisions/` 配下に provenance 関連あり要確認
- 関連 memory: `feedback_verify_data_contract.md`（データ変換時のソース確認原則）と相補的
