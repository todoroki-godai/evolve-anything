#!/usr/bin/env python3
"""パターン発見スクリプト。

usage.jsonl、errors.jsonl、sessions.jsonl、history.jsonl から
繰り返しパターンを検出し、スキル/ルール候補を生成する。
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path.home() / ".claude" / "rl-anything"

# パッケージ化後 (Phase 2): __file__ は scripts/lib/discover/__init__.py のため
# scripts/lib/ を sys.path に追加して plugin_root / line_limit / similarity 等を解決
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT

# HISTORY_DIR（旧 plugin 内 generations）は ADR-031 で撤去。
# accept/reject 履歴は optimize_history_store（DATA_DIR/optimize_history/<slug>）に集約。

# 閾値
BEHAVIOR_THRESHOLD = 5   # 行動パターン検出閾値
ERROR_THRESHOLD = 3       # エラーパターン検出閾値
REJECTION_THRESHOLD = 3   # 却下理由検出閾値
MISSED_SKILL_THRESHOLD = 2  # missed skill 検出閾値（セッション数）
# 成功軌跡からのスキル採掘 (SIRI ①, issue #291) の generalizability_score 下限。
# noise が増える場合はこの値を引き上げる（再評価条件）。
TRAJECTORY_SKILL_SCORE_THRESHOLD = 0.25

# 構造的制約は共通モジュールから取得
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
from agent_classifier import classify_agent_type
from line_limit import MAX_RULE_LINES, MAX_SKILL_LINES
from similarity import jaccard_coefficient, tokenize
from skill_triggers import extract_skill_triggers, normalize_skill_name

# Jaccard 照合閾値
JACCARD_THRESHOLD = 0.15

# Discover 振動防止用抑制リスト
SUPPRESSION_FILE = DATA_DIR / "discover-suppression.jsonl"


# 抑制リスト / JSONL ローダ / バリデータ / トークン抽出は discover/suppression.py に集約済み（後方互換のため再エクスポート）
from .suppression import (  # noqa: E402, F401
    load_jsonl,
    load_suppression_list,
    load_merge_suppression,
    add_merge_suppression,
    add_to_suppression_list,
    validate_skill_content,
    validate_rule_content,
    load_claude_reflect_data,
    _load_skill_tokens,
    _load_classify_usage_skill,
)


# 行動パターン検出 + Agent prompt 分類 + missed skill 検出 + constraint decay 検出は discover/patterns.py に集約済み（後方互換のため再エクスポート）
from .patterns import (  # noqa: E402, F401
    detect_behavior_patterns,
    detect_missed_skills,
    detect_constraint_decay,
    _classify_agent_prompts,
)



# エラー / 繰り返し correction / rejection 検出 + scope 判定は discover/errors.py に集約済み（後方互換のため再エクスポート）
from .errors import (  # noqa: E402, F401
    HOOK_CANDIDATE_THRESHOLD,
    detect_error_patterns,
    detect_repeated_correction_patterns,
    detect_rejection_patterns,
    determine_scope,
)


# Jaccard 照合 (enrich) は discover/enrich.py に集約済み（後方互換のため再エクスポート）
from .enrich import _enrich_patterns  # noqa: E402, F401


# 推奨 artifact 一覧 + 導入状態判定 + mitigation_metrics は discover/artifacts.py に集約済み（後方互換のため再エクスポート）
from .artifacts import (  # noqa: E402, F401
    RECOMMENDED_ARTIFACTS,
    detect_recommended_artifacts,
    detect_installed_artifacts,
    _compute_mitigation_metrics,
)



# run_discover オーケストレータ + CLI main は discover/runner.py に集約済み（後方互換のため再エクスポート）
from .runner import main, run_discover  # noqa: E402, F401


if __name__ == "__main__":
    main()
