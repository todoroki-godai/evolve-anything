#!/usr/bin/env python3
"""Pitfall 品質ゲート & ライフサイクル管理。

Candidate→New 2段階昇格、3層コンテキスト管理、
状態機械（Candidate→New→Active→Graduated→Pruned）、
回避回数ベース卒業判定を提供する。

Phase 5 で `pitfall_manager.py` (1230 行) をパッケージに分割:
- `parser.py`: markdown パース + 3層コンテキスト
- `recording.py`: 品質ゲート + 状態機械
- `detection.py`: Root-cause / 統合済み判定 / 自動検出 / TTL
- `preflight.py`: 行数ガード + Pre-flight スクリプト提案
- `rationalization.py`: 合理化防止テーブル
- `runner.py`: pitfall_hygiene オーケストレータ
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

# Cold 層自動アーカイブ定数
CAP_EXCEEDED_CONFIDENCE = 0.90
PREFLIGHT_MATURITY_RATIO = 0.50
# スクリプト化可能なカテゴリ
SCRIPTIFIABLE_CATEGORIES = frozenset({"action", "tool_use", "output"})

# skill_evolve 由来の閾値定数を再エクスポート（既存 importer 互換）
from skill_evolve import (  # noqa: E402,F401
    ACTIVE_PITFALL_CAP,
    CANDIDATE_PROMOTION_COUNT,
    ERROR_FREQUENCY_THRESHOLD,
    GRADUATED_TTL_DAYS,
    GRADUATION_THRESHOLDS,
    HOT_TIER_MAX_ITEMS,
    INTEGRATION_JACCARD_THRESHOLD,
    PITFALL_MAX_LINES,
    RATIONALIZATION_MIN_CORRECTIONS,
    RATIONALIZATION_OUTCOME_WINDOW_DAYS,
    RATIONALIZATION_SKIP_KEYWORDS,
    ROOT_CAUSE_JACCARD_THRESHOLD,
    STALE_ESCALATION_MONTHS,
    STALE_KNOWLEDGE_MONTHS,
)

# --- Pitfall パース / 3層コンテキスト (parser.py) ---
from .parser import (  # noqa: E402,F401
    _FIELD_RE,
    _PITFALL_HEADER_RE,
    _flush_item,
    get_cold_tier,
    get_hot_tier,
    get_warm_tier,
    parse_pitfalls,
    render_pitfalls,
)

# --- 品質ゲート / 状態機械 (recording.py) ---
from .recording import (  # noqa: E402,F401
    _make_pitfall_entry,
    _safe_read,
    _write_empty_template,
    find_matching_candidate,
    graduate_pitfall,
    promote_to_active,
    record_pitfall,
)

# --- Root-cause / 統合済み判定 / 自動検出 / TTL アーカイブ (detection.py) ---
from .detection import (  # noqa: E402,F401
    _STOP_WORDS,
    _split_sections_from_content,
    detect_archive_candidates,
    detect_integration,
    execute_archive,
    extract_pitfall_candidates,
    extract_root_cause_keywords,
)

# --- 行数ガード + Pre-flight スクリプト提案 (preflight.py) ---
from .preflight import (  # noqa: E402,F401
    _CATEGORY_TEMPLATE_MAP,
    _compute_line_guard,
    suggest_preflight_script,
)

# --- 合理化防止テーブル (rationalization.py) ---
from .rationalization import (  # noqa: E402,F401
    detect_rationalization_patterns,
    generate_rationalization_table,
)

# --- Pitfall 剪定オーケストレータ (runner.py) ---
from .runner import pitfall_hygiene  # noqa: E402,F401
from .injector import (  # noqa: E402,F401
    count_recent_errors,
    get_pitfall_for_skill,
    is_already_injected,
    mark_injected,
)
