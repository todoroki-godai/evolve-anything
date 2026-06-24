"""verbosity — 回答冗長性（verbosity）の学習ループ（#75）。

standalone（``~/.claude/verbosity/``）の仕組みを evolve-anything の正式機能へ統合する。
各部品は既存コンポーネントに 1:1 対応する移植+統合（発明ではない）:

- Stop hook ``hooks/record_verbosity.py``（ゼロ LLM・非ブロッキング）が長応答を
  ``verbosity_candidates.jsonl`` に記録（足切り 800 字・store_write barrier 経由・pj_slug 付与）。
- ``judge.py``（Haiku バッチ判定・dry-run 既定・llm-batch-guard 準拠）が未判定候補を
  「無駄に冗長か」+ 7 パターンで判定し、``verbosity_verdicts.jsonl`` に永続化。
  verbose=True は weak_signals に ``channel="verbosity"`` で emit（reflect 昇格フローに相乗り）。
- 多発パターンから ``rules/concise.md`` 追記案を suggestion 生成（auto-apply しない・protected）。
- ``query.py`` が冗長率 / パターン Top-N を集計（floor ゲート）し audit が advisory surface。

スコープ = PJ スコープ（record に pj_slug・read 側照合）+ fleet 集計（subagent_traces #38 と同型）。
"""

# 冗長パターンの語彙（standalone concise.md / judge.py と単一ソース）。
PATTERNS = {
    "preamble": "前置き・「承知しました」等のメタ文",
    "repetition": "同じ主張の繰り返し・言い換え重複",
    "filler": "水増し・情報を増やさない冗長な接続/修飾",
    "over_summary": "過剰なまとめ・締めの繰り返し",
    "restate_question": "質問・依頼文の不要な言い直し",
    "hedging": "過剰な前置き・保険・自己弁護",
    "meta": "不要な自己言及（「〜について説明します」等）",
}

# weak_signals の channel 名（verbosity 専用レーン・correction 系チャネルと分離）。
VERBOSITY_CHANNEL = "verbosity"

# 足切りゲート（hook と judge で共有する既定値）。これ未満は学習対象にしない。
DEFAULT_GATE_CHARS = 800
