#!/usr/bin/env python3
"""8層メモリ階層ルーティングユーティリティ（後方互換 re-export）。

実装は reflect_memory / reflect_routing に分割。
"""
from lib.frontmatter import parse_frontmatter as _parse_rule_frontmatter  # noqa: F401 — 共通化済み
from lib.reflect_memory import (  # noqa: F401
    find_claude_files,
    read_all_memory_entries,
    read_auto_memory,
    split_memory_sections,
)
from lib.reflect_routing import (  # noqa: F401
    LAST_SKILL_CONFIDENCE,
    PATHS_SUGGESTION_MIN_FILES,
    PathsSuggestion,
    _common_path_prefix,
    _resolve_skill_references_path,
    detect_project_signals,
    detect_side_effect_correction,
    suggest_auto_memory_topic,
    suggest_claude_file,
    suggest_paths_frontmatter,
)

__all__ = [
    "find_claude_files",
    "read_auto_memory",
    "read_all_memory_entries",
    "split_memory_sections",
    "detect_project_signals",
    "detect_side_effect_correction",
    "suggest_claude_file",
    "suggest_auto_memory_topic",
    "PathsSuggestion",
    "suggest_paths_frontmatter",
    "LAST_SKILL_CONFIDENCE",
    "PATHS_SUGGESTION_MIN_FILES",
]
