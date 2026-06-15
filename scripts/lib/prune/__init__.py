#!/usr/bin/env python3
"""淘汰スクリプト。

dead glob・zero invocation・重複の3基準でアーティファクトを検出し、
アーカイブを提案する。直接削除は行わない（MUST NOT）。
"""
import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from frontmatter import extract_description, parse_frontmatter
from similarity import filter_merge_group_pairs
from discover import load_merge_suppression
from skill_usage_stats import find_unused_global_skills, find_rarely_used_global_skills, find_nested_only_skills

from audit import (
    DATA_DIR,
    classify_artifact_origin,
    find_artifacts,
    load_usage_data,
    aggregate_usage,
    load_usage_registry,
    semantic_similarity_check,
)

ARCHIVE_DIR = DATA_DIR / "archive"

# 閾値定数 + evolve-state.json ロードは prune/config.py に集約済み（後方互換のため再エクスポート）
from .config import (  # noqa: E402, F401
    DEFAULT_DECAY_DAYS,
    DEFAULT_DECAY_THRESHOLD,
    CORRECTION_PENALTY,
    ZERO_INVOCATION_DAYS,
    USAGE_RECORDING_FIX_DATE,
    DEFAULT_MERGE_SIMILARITY_THRESHOLD,
    DEFAULT_INTERACTIVE_MERGE_THRESHOLD,
    DEFAULT_DRIFT_THRESHOLD,
    load_merge_similarity_threshold,
    load_interactive_merge_threshold,
    load_decay_threshold,
    load_drift_threshold,
)


# スキル frontmatter 解析 + 推薦ラベルは prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import (  # noqa: E402, F401
    _ARCHIVE_KEYWORDS,
    _KEEP_KEYWORDS,
    _KEEP_TRIGGER_THRESHOLD,
    _count_triggers,
    _enrich_candidate,
    extract_skill_summary,
    suggest_recommendation,
)


# corrections.jsonl の読み込み / decay-based クリーンアップは prune/corrections.py に集約済み（後方互換のため再エクスポート）
from .corrections import (  # noqa: E402, F401
    load_corrections,
    cleanup_corrections,
)


# 参照型判定 + 推定キャッシュ + 減衰スコア / pin は prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import (  # noqa: E402, F401
    _load_skill_type_cache,
    _save_skill_type_cache,
    _resolve_skill_md,
    is_reference_skill,
    _estimate_skill_type,
    compute_decay_score,
    is_pinned,
)



# dead glob / zero invocation / global safe / duplicate / decay 検出は prune/detection.py に集約済み（後方互換のため再エクスポート）
from .detection import (  # noqa: E402, F401
    _expand_glob_pattern,
    detect_dead_globs,
    detect_zero_invocations,
    make_zero_invocation_suppression_summary,
    safe_global_check,
    detect_duplicates,
    detect_decay_candidates,
    detect_retirement_candidates,
    zero_invocation_window_suppressed,
)





# skill 依存検査 (import / path ref) は prune/dependency.py に集約済み（後方互換のため再エクスポート）
from .dependency import (  # noqa: E402, F401
    SkillDependencyError,
    _IMPORT_RE_TEMPLATE,
    _list_skill_module_names,
    _git_grep_files,
    _is_git_repo,
    _iter_text_files,
    _python_grep_files_per_module,
    _python_grep_files,
    _is_excluded_referrer,
    check_import_dependencies,
)




# _is_skill_dir は prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import _is_skill_dir  # noqa: E402, F401



# archive 操作 + 重複マージ提案は prune/archive.py に集約済み（後方互換のため再エクスポート）
from .archive import (  # noqa: E402, F401
    archive_file,
    restore_file,
    list_archive,
    determine_primary,
    merge_duplicates,
)




# 参照型ドリフト評価は prune/drift.py に集約済み（後方互換のため再エクスポート）
from .drift import (  # noqa: E402, F401
    _gather_drift_context,
    detect_reference_drift,
    _evaluate_drift,
)


# run_prune オーケストレータは prune/runner.py に集約済み（後方互換のため再エクスポート）
from .runner import run_prune, main  # noqa: E402, F401


if __name__ == "__main__":
    main()
