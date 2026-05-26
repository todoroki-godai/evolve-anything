#!/usr/bin/env python3
"""スキル自己進化適性判定エンジン。

テレメトリ3軸 + LLMキャッシュ2軸の5項目スコアリングで
スキルの自己進化適性を判定する。

Phase 8 で `skill_evolve.py` (754 行) をパッケージに分割:
- `telemetry_scoring.py`: テレメトリ3軸 (frequency / diversity / evaluability)
- `llm_scoring.py`: LLM 2軸 (external_dependency / judgment_complexity)
- `classification.py`: 自己進化済み判定 / 検証系判定 / 適性分類 / アンチパターン /
  LLM スコアキャッシュ I/O
- `proposal.py`: 自己進化セクション + pitfalls.md テンプレート組み込み
  (`evolve_skill_proposal` / `_customize_template` / `apply_evolve_proposal`)
- `assessment.py`: 自己進化適性判定 (バルク + 単一スキル)
  (`skill_evolve_assessment` / `assess_single_skill` / `_find_project_dir`)
"""
from pathlib import Path

# --- 定数 (design Decision 9) ---

MEDIUM_SUITABILITY_THRESHOLD = 8
HIGH_SUITABILITY_THRESHOLD = 12
ROOT_CAUSE_JACCARD_THRESHOLD = 0.5
HOT_TIER_MAX_ITEMS = 5
ACTIVE_PITFALL_CAP = 10
GRADUATION_THRESHOLDS = {3: 10, 2: 5, 1: 3}
STALE_KNOWLEDGE_MONTHS = 6
ANTI_PATTERN_REJECTION_COUNT = 2
BAND_AID_THRESHOLD = 10
SUCCESS_PATTERN_LIMIT = 2
WARM_TOKEN_BUDGET = 1000
HOT_TOKEN_BUDGET = 500
HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.60
CANDIDATE_PROMOTION_COUNT = 2

# pitfall-lifecycle-automation 定数
INTEGRATION_JACCARD_THRESHOLD = 0.3
GRADUATED_TTL_DAYS = 30
STALE_ESCALATION_MONTHS = 3
PITFALL_MAX_LINES = 100
ERROR_FREQUENCY_THRESHOLD = 3

# 検証系スキル自動昇格キーワード
VERIFICATION_SKILL_KEYWORDS = [
    "verify", "validate", "check", "lint", "test", "qa", "audit",
    "assert", "inspect", "scan",
]

# 合理化防止テーブル定数 (superpowers-knowledge-integration)
RATIONALIZATION_MIN_CORRECTIONS = 3
RATIONALIZATION_SKIP_KEYWORDS = [
    "skip", "スキップ", "省略", "bypass", "later", "後で",
    "不要", "unnecessary", "without", "なし", "いらない",
    "面倒", "time", "時間がない", "急ぎ",
]
RATIONALIZATION_OUTCOME_WINDOW_DAYS = 30

# LLMキャッシュ
# `<repo>/scripts/lib/skill_evolve/__init__.py` → `<repo>/scripts`
_plugin_root = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path.home() / ".claude" / "rl-anything"
CACHE_FILE = DATA_DIR / "skill-evolve-cache.json"

# --- 分類 / アンチパターン / キャッシュヘルパ (classification.py / Phase 8 Slice 3) ---
from .classification import (  # noqa: E402,F401
    _file_hash,
    _load_cache,
    _save_cache,
    is_self_evolved_skill,
    is_verification_skill,
    classify_suitability,
    detect_anti_patterns,
)

# --- テレメトリ3軸 (telemetry_scoring.py / Phase 8 Slice 1) ---
from .telemetry_scoring import (  # noqa: E402,F401
    TELEMETRY_LOOKBACK_DAYS,
    _score_execution_frequency,
    _score_failure_diversity,
    _score_output_evaluability,
    compute_telemetry_scores,
)

# --- LLM 2軸 (llm_scoring.py / Phase 8 Slice 2) ---
from .llm_scoring import (  # noqa: E402,F401
    _EXTERNAL_DEPENDENCY_KEYWORDS,
    _count_external_keywords,
    _score_external_dependency,
    _score_judgment_complexity_llm,
    compute_llm_scores,
)

# --- 変換提案 (proposal.py / Phase 8 Slice 4) ---
from .proposal import (  # noqa: E402,F401
    evolve_skill_proposal,
    _customize_template,
    apply_evolve_proposal,
    count_diff_lines,
)

# --- 自己進化適性判定 (assessment.py / Phase 8 Slice 4) ---
from .assessment import (  # noqa: E402,F401
    _find_project_dir,
    skill_evolve_assessment,
    assess_single_skill,
)
