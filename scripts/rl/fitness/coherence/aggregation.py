#!/usr/bin/env python3
"""Coherence Score の統合スコア算出 + audit レポート用フォーマット。

`scripts/rl/fitness/coherence/__init__.py` から切り出された
集約 (compute / format / advice) ロジック (Phase 10 / Slice 4)。
"""
from pathlib import Path
from typing import Any, Dict, List

from .scoring_basic import score_coverage, score_consistency
from .scoring_advanced import score_completeness, score_efficiency


def _weights() -> Dict[str, float]:
    """`coherence.WEIGHTS` を遅延参照する（monkeypatch 互換）。"""
    from . import WEIGHTS  # noqa: WPS433
    return WEIGHTS


def _thresholds() -> Dict[str, Any]:
    """`coherence.THRESHOLDS` を遅延参照する（monkeypatch 互換）。"""
    from . import THRESHOLDS  # noqa: WPS433
    return THRESHOLDS


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

    WEIGHTS = _weights()
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
    THRESHOLDS = _thresholds()
    lines = [f"## Environment Coherence Score: {result['overall']:.2f}", ""]

    for axis in ("coverage", "consistency", "completeness", "efficiency"):
        score = result[axis]
        bar_filled = int(score * 20)
        bar_empty = 20 - bar_filled
        bar = "█" * bar_filled + "░" * bar_empty

        # 低スコア軸への注釈
        annotation = ""
        if score < THRESHOLDS["advice_threshold"]:
            axis_details = result["details"].get(axis, {})
            issues = _summarize_issues(axis, axis_details)
            if issues:
                annotation = f" ← {issues}"

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
