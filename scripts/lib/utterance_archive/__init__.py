"""utterance_archive — 全PJ human 発話の恒久アーカイブ（#430, Phase B）。

設計の SoT: docs/evolve/utterance-archive-430-415-design.md の「Phase B」。

構成:
- extractor : transcript jsonl から human 発話を抽出（決定論・ゼロ LLM）
- store     : utterances.db（DuckDB）のスキーマ・接続・INSERT/upsert
- ingest    : ~/.claude/projects/*/*.jsonl → utterances.db の増分取り込み
- query     : query_utterances(pj_slug 必須) + query_utterances_all_projects()

writer は batch ingest のみ＝ hot path ゼロ。DATA_DIR は ADR-042 resolver 経由。
"""
from __future__ import annotations

from .extractor import (
    EXCLUDED_PJ_SLUGS,
    EXTRACTOR_VERSION,
    LONG_PASTE_THRESHOLD,
    Utterance,
    extract_utterances,
    pj_slug_from_dir_name,
)

__all__ = [
    "EXCLUDED_PJ_SLUGS",
    "EXTRACTOR_VERSION",
    "LONG_PASTE_THRESHOLD",
    "Utterance",
    "extract_utterances",
    "pj_slug_from_dir_name",
]
