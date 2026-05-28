---
date: 2026-05-28
status: accepted
---
# PJ 横断 memory recall は keyword 決定論 engine（vector 非採用）

## Context

複数 PJ に跨って蓄積した知見（`~/.claude/projects/<pj>/memory/*.md`）を、現在の PJ から
横断的に引きたいというニーズがある。汎用 AI memory layer の gbrain（PGLite + pgvector、
hybrid retrieval、knowledge graph、80+ MCP tools）を試験導入したが、評価の結果:

- 欲しいのは「PJ 横断 recall」1 点のみ。gbrain の vector / hybrid / KG / MCP 群は過剰。
- 対象コーパス実測（2026-05-28）= **14 PJ / 168 markdown / 合計 760K**。760K は全文が
  1 プロンプト（< 200K tokens）に収まる規模で、vector の本来の価値（コーパスがコンテキストに
  載らないから近似検索する）が発生していない。
- vector が本当に効く PJ（docs-platform handbook / sys-bots RAG）は実在するが、それらは
  製品が自前 RAG を持つべきで、rl-anything が汎用 vector store を抱えるのは関心事の混同。

## Decision

rl-anything に **keyword ベースの決定論的な PJ 横断 recall** を `bin/rl-fleet recall` として実装する。

1. **1段・決定論 engine（LLM rerank なし）**: keyword/token prefilter（stdlib `re`/`pathlib`）→
   TF + frontmatter description/filename ブーストで rank → 構造化出力（`--json`）。
   recall の消費者は呼び出し側 assistant（＝最強の reranker）なので、CLI で再度 LLM を
   呼ぶ rerank の二重化は避ける。非決定性（順位揺れで再現困難バグ）・レイテンシ・課金・
   テストの LLM mock 負債を回避し、engine を決定論に保つ。
2. **列挙は memory dir 存在ベースの別経路**（`enumerate_memory_dirs()`）: 既存
   `enumerate_projects()` は `_is_plugin_enabled` で rl-anything 有効 PJ に絞るため、recall に
   流用すると未導入 PJ の memory が静かに消え横断性を殺す。`memory/` の存在だけを条件にする。
3. **embedding / vector は非採用**。
4. **frontmatter パース堅牢性**: 不正/欠落でも本文 grep フォールバック。delimiter があるのに
   壊れているファイルは stderr に警告（静かな検索漏れを防ぐ）。
5. **gbrain は外す**（MCP 登録解除、clone は dormant 保持で可逆）。

## Consequences

- 新インフラ・新依存ゼロ（Python stdlib + 既存 PyYAML）。決定論なので単体テストで LLM mock 不要。
- 実測 wall time ~0.1s / 168 ファイル。
- **再検討トリガー（vector 導入を再評価する転換点）**: 総量が 2-3MB / 数百K tokens を超える /
  キーワード一致しない semantic recall が常用ニーズになる / recall のインタラクティブ多用で
  grep+読みのレイテンシが体感問題になる。いずれも現状未到達。語彙ゆれは frontmatter への
  synonym/tag 追加という安価な手で延命できる。

## References

- 設計: `~/.gstack/projects/todoroki-godai-rl-anything/todoroki-main-design-20260528-133406.md`
- 関連 ADR: [022 fleet 観測・介入](022-fleet-observation-plus-intervention.md)
- 実装: `scripts/lib/fleet/recall.py`, `scripts/lib/fleet/project_loader.py::enumerate_memory_dirs`
