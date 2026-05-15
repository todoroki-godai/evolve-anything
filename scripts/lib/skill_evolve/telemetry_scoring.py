"""テレメトリ3軸スコアリング (frequency / diversity / evaluability)。

Phase 8 / Slice 1 で `skill_evolve.py` から切り出し。
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Set


# テレメトリの集計期間（日）
TELEMETRY_LOOKBACK_DAYS = 30

# `<repo>/scripts/lib/skill_evolve/telemetry_scoring.py` → `<repo>/scripts`
_plugin_root = Path(__file__).resolve().parent.parent.parent


def _score_execution_frequency(usage_count: int) -> int:
    """実行頻度スコア (1-3)。直近30日の呼び出し回数。"""
    if usage_count >= 16:
        return 3  # 日常的
    if usage_count >= 4:
        return 2  # 週数回
    return 1  # 月3回以下


def _score_failure_diversity(error_categories: Set[str]) -> int:
    """失敗多様性スコア (1-3)。ユニーク根本原因カテゴリ数。"""
    count = len(error_categories)
    if count >= 4:
        return 3
    if count >= 2:
        return 2
    return 1


def _score_output_evaluability(usage_count: int, error_count: int) -> int:
    """出力評価可能性スコア (1-3)。成功率から推定。"""
    if usage_count == 0:
        return 1
    success_rate = (usage_count - error_count) / usage_count
    if success_rate <= 0.5:
        return 3  # 明確な品質差がある
    if success_rate <= 0.85:
        return 2
    return 1  # ほぼ成功＝評価困難


def compute_telemetry_scores(
    skill_name: str,
    *,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """テレメトリ3軸のスコアを計算する。

    Returns:
        {"frequency": int, "diversity": int, "evaluability": int,
         "usage_count": int, "error_count": int, "error_categories": [...]}
    """
    sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
    from telemetry_query import query_usage, query_errors

    since = (datetime.now(timezone.utc) - timedelta(days=TELEMETRY_LOOKBACK_DAYS)).isoformat()

    usage_records = query_usage(project=project, since=since)
    error_records = query_errors(project=project, since=since)

    # スキル名でフィルタ
    usage_count = sum(
        1 for r in usage_records
        if r.get("skill_name", "") == skill_name
    )
    skill_errors = [
        r for r in error_records
        if r.get("skill_name", "") == skill_name
    ]
    error_count = len(skill_errors)

    # エラーの根本原因カテゴリ抽出
    error_categories: Set[str] = set()
    for err in skill_errors:
        cat = err.get("root_cause_category", err.get("error_type", "unknown"))
        error_categories.add(cat)

    return {
        "frequency": _score_execution_frequency(usage_count),
        "diversity": _score_failure_diversity(error_categories),
        "evaluability": _score_output_evaluability(usage_count, error_count),
        "usage_count": usage_count,
        "error_count": error_count,
        "error_categories": sorted(error_categories),
    }
