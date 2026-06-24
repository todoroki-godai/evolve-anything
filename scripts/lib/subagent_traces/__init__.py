"""subagent_traces — subagent の内部軌跡ストア（#38）。

親セッションの error_count しか見ない既存 outcome 帰属（outcome_attribution 等）の盲点
— impl-worker が内部で error 連発しても最終成功すれば「一発成功」と誤記録される — を
塞ぐため、subagent transcript の tool_use / tool_result / is_error 列をパースして
「内部で一発成功したか」を per-agent_type で advisory 集計する。

MVP: jsonl ストア（DuckDB checkpoint pitfall 回避）+ extractor + 増分 ingest +
advisory audit section + evolve batch 配線。決定論・ゼロ LLM。

データソース:
- subagents.jsonl（DATA_DIR・全PJ共通）: 1 行 = subagent 1 spawn。
  agent_transcript_path で transcript jsonl への絶対パスを持つ（掃除済みで不在のこともある）。
- transcript jsonl: message.content の list ブロック（tool_use / tool_result / text）。

store_write barrier（ADR-049）経由で書き、read は read-only 純度（ファイルを作らない）。
pj_slug スコープ（全PJ共通 DATA_DIR ゆえ read 側で filter）。
"""
