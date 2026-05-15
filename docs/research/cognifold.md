# tech-eval: Cognifold — Always-On Proactive Memory via Cognitive Folding (arXiv:2605.13438)

- **評価日**: 2026-05-15
- **対象**: Wang et al., "Cognifold: Always-On Proactive Memory via Cognitive Folding" (2026-05-14)
- **結論**: 🟢 **概念は注目に値、保留**（コア発想は MEMORY.md / token_usage_store の世代交代候補だが、実装複雑度と現状運用の負債のバランスが未確認）
- **再評価トリガー**: MEMORY.md のエントリ数が 80 を超える / session 横断のメモリ復元レイテンシが運用課題になる
- **実体**: エージェントの長期記憶を「常時更新 + 折り畳み圧縮 + 必要時即時復元」する手法。脳神経科学からインスパイア

## rl-anything 側の現行実装と照合

| 論文の概念 | rl-anything 側の対応 | 状態 |
|-----------|---------------------|------|
| 長期メモリの永続化 | `MEMORY.md` (auto-memory) + `token_usage_store.py` (DuckDB SoR) | ✅ |
| 古いメモリの陳腐化検出 | `memory_temporal.py` (`is_stale`, `is_superseded`) | ✅ |
| stale entry の自動診断 | `layer_diagnose.py` (stale_memory 検出) | ✅ |
| **未使用メモリの背景圧縮** | — | 🔶 未実装 |
| **必要時の即時復元・再活性化** | — | 🔶 未実装 (現状は MEMORY.md 全体を毎セッション load) |
| Always-on の予測的読み込み | — | 🔶 未実装 |

## 採否理由（保留の根拠）

採用に踏み切れない理由:
1. **MEMORY.md は現状 40 エントリ規模で、全部 load しても token コストは許容範囲**。「折り畳み」が必要な圧迫はまだ発生していない
2. **論文側の実装複雑度が不明**: 「即時復元」を実装すると永続化フォーマット / index 設計 / ttl 管理が必要で、現状の単純な MD 運用から大きく逸脱
3. **既存の `is_stale` / `is_superseded` で代替可能**: 折り畳む代わりに `archive_*.md` に追い出す現運用が機能している
4. **token_usage_store は別レイヤー**: 認知折り畳み対象は MEMORY.md 側であり、DuckDB store は別目的（ingest 用 SoR）

採用する価値があるかもしれない点:
- **エントリ数が増えた時の自動圧縮戦略**: 現在は手動で `archive_change_history.md` に移しているが、これを自動化する hook は将来必要
- **読み出し時の "活性化"**: MEMORY.md は今全件 load だが、関連エントリのみ読み込む lazy load 構造に進化させる場合の参考になる

## 借りる価値のあるアイデア

| アイデア | 適用先候補 |
|---------|-----------|
| 未参照エントリの自動 archive | `audit` skill に統合する形で stale entry を archive_*.md に移すルーチン |
| 関連エントリのみ activate | future work — 現状の MEMORY.md 全件 load を疑問視する材料 |
| メモリの "wake on demand" | DuckDB store と組み合わせた段階的詳細化 (L1 = MEMORY.md, L2 = archive, L3 = DuckDB) |

## 推奨アクション

| 概念 | 推奨度 | アクション | 再評価条件 |
|------|--------|------------|------------|
| 認知折り畳み実装 | 低 | 採用しない | MEMORY.md > 80 エントリ or session start token cost が体感負担 |
| 自動 archive ルーチン | 中（独立施策） | Issue 化候補 (現状の手動 archive を自動化) | 直近 3 ヶ月で archive 操作が 5 回以上発生したら |

## 関連

- 現行コード: `scripts/lib/memory_temporal.py`, `scripts/lib/layer_diagnose.py:139-205`, `scripts/lib/token_usage_store.py`
- 関連メモリ: `~/.claude/projects/-Users-todoroki-tools-rl-anything/memory/archive_change_history.md` (既存の手動 archive)
