#!/usr/bin/env python3
"""環境の健康診断スクリプト。

全 skills / rules / memory の棚卸し + 行数チェック + 使用状況集計を行い、
1画面レポートを出力する。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from rl_common import DATA_DIR  # noqa: F401 — re-exported for backward compat (audit.DATA_DIR / bloat_control / test patches)
from reflect_utils import read_all_memory_entries, read_auto_memory, split_memory_sections
from hardcoded_detector import detect_hardcoded_values
from frontmatter import count_content_lines
from path_extractor import extract_paths_outside_codeblocks as _extract_paths_outside_codeblocks, KNOWN_DIR_PREFIXES
from skill_origin import (
    get_plugin_skill_names as _so_get_plugin_skill_names,
    invalidate_cache as _so_invalidate_cache,
)

# 行数制限 — line_limit.py を Single Source of Truth として参照
from line_limit import (
    MAX_PROJECT_RULE_LINES,
    MAX_RULE_LINES,
    MAX_SKILL_LINES,
    NEAR_LIMIT_RATIO,
)

# LIMITS / _STOPWORDS は audit/_constants.py に集約済み（後方互換のため再エクスポート）
from ._constants import LIMITS, _STOPWORDS  # noqa: F401

# DATA_DIR は rl_common.DATA_DIR を再エクスポート（L19 の import 参照）
# - CLAUDE_PLUGIN_DATA env var サポート（cross-project / fleet 用途）
# - 真の源を rl_common.py に一本化、bloat_control.py と test patch の互換維持
#   詳細: docs/decisions/022-data-dir-unification.md（予定）

# KNOWN_DIR_PREFIXES は path_extractor から import 済み

# 分類ロジックは audit/classification.py に集約済み（後方互換のため再エクスポート）
# テストが `audit._plugin_skill_map_cache` を直接セットしていた箇所は
# `audit.classification._plugin_skill_map_cache` に追従させること（Phase 2 第五弾）。
from .classification import (  # noqa: F401
    _load_plugin_skill_map,
    _build_plugin_prefixes,
    _load_plugin_skill_names,
    classify_usage_skill,
    classify_artifact_origin,
)


# find_artifacts / check_line_limits は audit/artifacts.py に集約済み（後方互換のため再エクスポート）
from .artifacts import find_artifacts, check_line_limits, check_python_source_budgets  # noqa: F401


# Memory verification functions are extracted to audit/memory.py
# 後方互換のため audit パッケージから直接 import 可能にする
from .memory import (  # noqa: F401, E402
    _extract_section_keywords,
    _find_archive_mentions,
    _is_project_specific_section,
    build_memory_verification_context,
    build_memory_health_section,
    build_temporal_memory_warnings,
)


# Usage 集計は audit/usage.py に集約済み（後方互換のため再エクスポート）
# テストが `audit.load_usage_data` 等を patch している箇所は __init__.py の
# 名前空間を上書きするため引き続き機能する（Phase 2 第七弾）。
from .usage import (  # noqa: F401
    _BUILTIN_TOOLS,
    load_usage_data,
    _is_openspec_skill,
    _is_plugin_skill,
    aggregate_usage,
    aggregate_plugin_usage,
    aggregate_contribution_scores,
)


# Scope advisory / 重複検出 / 類似度は audit/scope.py に集約済み（後方互換のため再エクスポート）
from .scope import (  # noqa: F401
    detect_duplicates_simple,
    semantic_similarity_check,
    load_usage_registry,
    scope_advisory,
)


# Quality trends は audit/quality.py に分離 (Phase 2 第三弾)
from .quality import (  # noqa: F401, E402
    build_quality_trends_section,
    generate_sparkline,
    load_quality_baselines,
)


# gstack 関連は audit/gstack.py に分離 (Phase 2 第二弾)
from .gstack import (  # noqa: F401, E402
    _FALLBACK_GSTACK_LIFECYCLE,
    _FALLBACK_GSTACK_SKILL_PHASE_MAP,
    _FLOW_CHAIN_FILE,
    _GSTACK_LIFECYCLE,
    _GSTACK_SKILL_NAMES,
    _GSTACK_SKILL_PHASE_MAP,
    _is_gstack_skill,
    _load_flow_chain_phases,
    _match_gstack_phase,
    build_gstack_analytics_section,
)


# Issues collection は audit/issues.py に分離 (Phase 2 第四弾)
from .issues import (  # noqa: F401, E402
    _is_user_invocable_heuristic,
    collect_issues,
    detect_untagged_reference_candidates,
)


# Sections (Constitutional / Token / Test Guard) は audit/sections.py に集約済み（後方互換のため再エクスポート）
from .sections import (  # noqa: F401
    _format_constitutional_report,
    _short_int,
    build_token_consumption_section,
    _build_test_guard_section,
)


# generate_report は audit/report.py に集約済み（後方互換のため再エクスポート）
from .report import generate_report  # noqa: F401

# gstack の cross-project loader は orchestrator から間接利用 (テスト後方互換)
from .gstack import _load_global_retro  # noqa: F401

# Orchestrator (run_audit / history 記録 / Growth Report) は audit/orchestrator.py に集約済み
# テスト後方互換: audit._AUDIT_HISTORY_FILE / audit.run_audit / audit._build_growth_report 等を維持
from .orchestrator import (  # noqa: F401
    _AUDIT_HISTORY_FILE,
    _MAX_AUDIT_HISTORY,
    _DEGRADATION_THRESHOLD,
    _record_audit_completion,
    _extract_score_from_report,
    _append_audit_history,
    _check_degradation,
    run_audit,
    _build_growth_report,
)

def main() -> None:
    """bin/rl-audit エントリポイント。"""
    import argparse as _argparse

    _parser = _argparse.ArgumentParser(description="環境の健康診断")
    _parser.add_argument("project", nargs="?", default=None, help="プロジェクトディレクトリ")
    _parser.add_argument("--skip-rescore", action="store_true", help="品質計測をスキップ")
    _parser.add_argument("--memory-context", action="store_true", help="MEMORY 検証コンテキストを JSON 出力")
    _parser.add_argument("--coherence-score", action="store_true", help="Coherence Score セクションを表示")
    _parser.add_argument("--telemetry-score", action="store_true", help="Telemetry Score セクションを表示")
    _parser.add_argument("--constitutional-score", action="store_true", help="Constitutional Score セクションを表示")
    _parser.add_argument("--pipeline-health", action="store_true", help="Pipeline Health セクションを表示")
    _parser.add_argument("--growth", action="store_true", help="NFD Growth Report セクションを表示")
    _args = _parser.parse_args()
    if _args.memory_context:
        proj = Path(_args.project) if _args.project else Path.cwd()
        ctx = build_memory_verification_context(proj)
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        print(run_audit(_args.project, skip_rescore=_args.skip_rescore, coherence_score=_args.coherence_score, telemetry_score=_args.telemetry_score, constitutional_score=_args.constitutional_score, pipeline_health=_args.pipeline_health, growth=_args.growth))


if __name__ == "__main__":
    main()
