#!/usr/bin/env python3
"""環境全体の構造的整合性を測る Coherence Score。

4軸（Coverage / Consistency / Completeness / Efficiency）で
LLM コストゼロの静的分析スコア（0.0〜1.0）を算出する。
"""
# Phase 10 / Slice 1: artifacts ヘルパーは coherence/artifacts.py に切り出し済み。
# 後方互換のため再エクスポート（テスト・外部 importer の `from coherence import _ensure_paths` 等が依存）。
from .artifacts import (  # noqa: F401
    _ensure_paths,
    _is_plugin_project,
    _find_project_artifacts,
    _find_artifacts_local,
    _plugin_root,
)

try:
    from .config import COHERENCE_THRESHOLDS as THRESHOLDS
except ImportError:
    THRESHOLDS = {
        "skill_min_lines": 50,
        "rule_max_lines": 3,
        "claude_md_max_lines": 200,
        "near_limit_pct": 0.80,
        "unused_skill_days": 30,
        "advice_threshold": 0.7,
    }

WEIGHTS = {
    "coverage": 0.25,
    "consistency": 0.30,
    "completeness": 0.25,
    "efficiency": 0.20,
}

# Phase 10 / Slice 2: Coverage / Consistency 軸スコアリングは coherence/scoring_basic.py に切り出し済み。
# 後方互換のため再エクスポート。
from .scoring_basic import (  # noqa: F401
    _COVERAGE_ITEMS,
    score_coverage,
    score_consistency,
    _extract_mentioned_skills,
    _check_memory_paths,
    _PATH_PATTERN,
)


# Phase 10 / Slice 3: Completeness / Efficiency 軸スコアリングは coherence/scoring_advanced.py に切り出し済み。
# 後方互換のため再エクスポート。
from .scoring_advanced import (  # noqa: F401
    score_completeness,
    score_efficiency,
    _get_used_skills,
)


# Phase 10 / Slice 4: 統合スコア + audit レポートフォーマットは coherence/aggregation.py に切り出し済み。
# 後方互換のため再エクスポート。Phase 10 完了。
from .aggregation import (  # noqa: F401
    compute_coherence_score,
    format_coherence_report,
    _summarize_issues,
    _build_advice,
)
