#!/usr/bin/env python3
"""環境全体の構造的整合性を測る Coherence Score。

4軸（Coverage / Consistency / Completeness / Efficiency）で
LLM コストゼロの静的分析スコア（0.0〜1.0）を算出する。
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ---------- 統合スコア ----------

def compute_coherence_score(project_dir: Path) -> Dict[str, Any]:
    """4軸の重み付き平均で統合 Coherence Score を算出する。

    Returns:
        {
            "overall": float,
            "coverage": float,
            "consistency": float,
            "completeness": float,
            "efficiency": float,
            "details": {
                "coverage": {...},
                "consistency": {...},
                "completeness": {...},
                "efficiency": {...},
            },
        }
    """
    project_dir = Path(project_dir)

    cov_score, cov_details = score_coverage(project_dir)
    con_score, con_details = score_consistency(project_dir)
    com_score, com_details = score_completeness(project_dir)
    eff_score, eff_details = score_efficiency(project_dir)

    overall = (
        WEIGHTS["coverage"] * cov_score
        + WEIGHTS["consistency"] * con_score
        + WEIGHTS["completeness"] * com_score
        + WEIGHTS["efficiency"] * eff_score
    )

    return {
        "overall": round(overall, 4),
        "coverage": cov_score,
        "consistency": con_score,
        "completeness": com_score,
        "efficiency": eff_score,
        "details": {
            "coverage": cov_details,
            "consistency": con_details,
            "completeness": com_details,
            "efficiency": eff_details,
        },
    }


def format_coherence_report(result: Dict[str, Any]) -> List[str]:
    """Coherence Score を audit レポート用にフォーマットする。"""
    lines = [f"## Environment Coherence Score: {result['overall']:.2f}", ""]

    for axis in ("coverage", "consistency", "completeness", "efficiency"):
        score = result[axis]
        bar_filled = int(score * 20)
        bar_empty = 20 - bar_filled
        bar = "\u2588" * bar_filled + "\u2591" * bar_empty

        # 低スコア軸への注釈
        annotation = ""
        if score < THRESHOLDS["advice_threshold"]:
            axis_details = result["details"].get(axis, {})
            issues = _summarize_issues(axis, axis_details)
            if issues:
                annotation = f" \u2190 {issues}"

        lines.append(
            f"{axis.capitalize():14s} {score:.2f} {bar}{annotation}"
        )

    # advice_threshold 未満の軸の詳細
    low_axes = [
        a for a in ("coverage", "consistency", "completeness", "efficiency")
        if result[a] < THRESHOLDS["advice_threshold"]
    ]
    if low_axes:
        lines.append("")
        lines.append("### Improvement Advice")
        for axis in low_axes:
            axis_details = result["details"].get(axis, {})
            advice = _build_advice(axis, axis_details)
            if advice:
                lines.append(f"**{axis.capitalize()}:**")
                for item in advice:
                    lines.append(f"  - {item}")

    lines.append("")
    return lines


def _summarize_issues(axis: str, details: Dict[str, Any]) -> str:
    """低スコア軸の概要を1行で返す。"""
    summaries = []
    for key, val in details.items():
        if isinstance(val, dict) and not val.get("pass", True):
            summaries.append(key.replace("_", " "))
    return ", ".join(summaries) if summaries else ""


def _build_advice(axis: str, details: Dict[str, Any]) -> List[str]:
    """低スコア軸の改善アドバイスを返す。"""
    advice = []
    for key, val in details.items():
        if not isinstance(val, dict) or val.get("pass", True):
            continue
        if key == "skill_existence":
            missing = val.get("missing", [])
            advice.append(f"CLAUDE.md で言及されているが実在しない Skill: {', '.join(missing)}")
        elif key == "memory_paths":
            stale = val.get("stale", [])
            advice.append(f"MEMORY.md 内の存在しないパス参照: {len(stale)} 件")
        elif key == "trigger_duplicates":
            dups = val.get("duplicates", [])
            for d in dups:
                advice.append(f"トリガー '{d['trigger']}' が {', '.join(d['skills'])} で重複")
        elif key == "skill_quality":
            issues = val.get("issues", [])
            advice.append(f"品質基準を満たさない Skill: {len(issues)} 件")
        elif key == "rule_compliance":
            issues = val.get("issues", [])
            advice.append(f"行数制約を超える Rule: {len(issues)} 件")
        elif key == "claude_md_size":
            advice.append(f"CLAUDE.md が {val.get('lines', '?')}/{val.get('limit', '?')} 行")
        elif key == "hardcoded_values":
            advice.append(f"ハードコード値: {val.get('count', 0)} 件")
        elif key == "duplicate_skills":
            advice.append(f"重複 Skill: {len(val.get('pairs', []))} 件")
        elif key == "near_limit":
            advice.append(f"肥大化警告: {len(val.get('files', []))} ファイル")
        elif key == "unused_skills":
            skills = val.get("skills", [])
            advice.append(f"未使用 Skill: {', '.join(skills)}")
    return advice
