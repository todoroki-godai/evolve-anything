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


# ---------- Completeness ----------

def score_completeness(project_dir: Path) -> Tuple[float, Dict[str, Any]]:
    """定義されたものが動くレベルで完成しているかチェックする。

    チェック項目:
    1. Skill 行数（最低50行）と必須セクション（Usage, Steps）
    2. Rule 行数制約（3行以内）
    3. CLAUDE.md 行数（200行以内）
    4. ハードコード値検出

    Returns:
        (score, details)
    """
    details: Dict[str, Any] = {
        "skill_quality": {"pass": True, "issues": []},
        "rule_compliance": {"pass": True, "issues": []},
        "claude_md_size": {"pass": True},
        "hardcoded_values": {"pass": True, "count": 0},
    }

    checks_total = 0
    checks_passed = 0

    artifacts = _find_project_artifacts(project_dir)

    # 1. Skill 行数と必須セクション
    if artifacts["skills"]:
        checks_total += 1
        skill_issues = []
        for skill_path in artifacts["skills"]:
            try:
                content = skill_path.read_text(encoding="utf-8")
                lines = content.count("\n") + 1
            except (OSError, UnicodeDecodeError):
                continue
            issues_for_skill: List[str] = []
            if lines < THRESHOLDS["skill_min_lines"]:
                issues_for_skill.append(f"{lines} lines (min {THRESHOLDS['skill_min_lines']})")
            # 必須セクション（## Usage, **Usage** 等のバリエーション対応）
            for section in ("Usage", "Steps"):
                if not re.search(
                    rf"^#{{1,4}}\s+\*?\*?{section}\*?\*?|^\*\*{section}\*\*",
                    content,
                    re.MULTILINE | re.IGNORECASE,
                ):
                    issues_for_skill.append(f"missing section: {section}")
            if issues_for_skill:
                skill_issues.append({
                    "file": str(skill_path),
                    "issues": issues_for_skill,
                })
        if skill_issues:
            details["skill_quality"] = {"pass": False, "issues": skill_issues}
        else:
            checks_passed += 1

    # 2. Rule 行数制約
    if artifacts["rules"]:
        checks_total += 1
        rule_issues = []
        for rule_path in artifacts["rules"]:
            try:
                content = rule_path.read_text(encoding="utf-8")
                # 空行と見出し行を除いた実質行数
                meaningful_lines = [
                    l for l in content.splitlines()
                    if l.strip() and not l.strip().startswith("#")
                ]
                if len(meaningful_lines) > THRESHOLDS["rule_max_lines"]:
                    rule_issues.append({
                        "file": str(rule_path),
                        "lines": len(meaningful_lines),
                    })
            except (OSError, UnicodeDecodeError):
                continue
        if rule_issues:
            details["rule_compliance"] = {"pass": False, "issues": rule_issues}
        else:
            checks_passed += 1

    # 3. CLAUDE.md 行数
    if artifacts["claude_md"]:
        checks_total += 1
        try:
            content = artifacts["claude_md"].read_text(encoding="utf-8")
            lines = content.count("\n") + 1
            if lines > THRESHOLDS["claude_md_max_lines"]:
                details["claude_md_size"] = {
                    "pass": False,
                    "lines": lines,
                    "limit": THRESHOLDS["claude_md_max_lines"],
                }
            else:
                checks_passed += 1
        except (OSError, UnicodeDecodeError):
            checks_passed += 1

    # 4. ハードコード値検出
    _ensure_paths()
    from hardcoded_detector import detect_hardcoded_values
    all_files = list(artifacts["skills"]) + list(artifacts["rules"])
    if all_files:
        checks_total += 1
        hardcoded_count = 0
        for f in all_files:
            detections = detect_hardcoded_values(str(f))
            hardcoded_count += len(detections)
        if hardcoded_count > 0:
            details["hardcoded_values"] = {"pass": False, "count": hardcoded_count}
        else:
            checks_passed += 1

    score = checks_passed / checks_total if checks_total > 0 else 1.0
    return round(score, 4), details


# ---------- Efficiency ----------

def score_efficiency(project_dir: Path, *, data_dir: Optional[Path] = None) -> Tuple[float, Dict[str, Any]]:
    """冗長さや肥大化がないかチェックする。

    チェック項目:
    1. 意味的重複 Skill
    2. near-limit（80% 超え）
    3. 未使用 Skill（30日以上ゼロ invoke）
    4. 孤立 Rule（CLAUDE.md で参照されていない）

    Returns:
        (score, details)
    """
    details: Dict[str, Any] = {
        "duplicate_skills": {"pass": True, "pairs": []},
        "near_limit": {"pass": True, "files": []},
        "unused_skills": {"pass": True, "skills": [], "skipped": False},
    }

    checks: List[bool] = []

    _ensure_paths()
    from audit import detect_duplicates_simple, LIMITS, NEAR_LIMIT_RATIO

    # プロジェクト限定のアーティファクト探索（グローバル ~/.claude/ を含めない）
    artifacts = _find_artifacts_local(project_dir)

    # 1. 意味的重複 Skill
    duplicates = detect_duplicates_simple(artifacts)
    if duplicates:
        details["duplicate_skills"] = {
            "pass": False,
            "pairs": [{"name": d["name"], "paths": d["paths"]} for d in duplicates],
        }
        checks.append(False)
    else:
        checks.append(True)

    # 2. near-limit
    near_limit_files = []
    for category, limit_key in [("skills", "SKILL.md"), ("rules", "rules"), ("claude_md", "CLAUDE.md")]:
        for path in artifacts.get(category, []):
            try:
                content = path.read_text(encoding="utf-8")
                lines = content.count("\n") + 1
                limit = LIMITS.get(limit_key, 500)
                if lines >= int(limit * NEAR_LIMIT_RATIO):
                    near_limit_files.append({
                        "file": str(path),
                        "lines": lines,
                        "limit": limit,
                    })
            except (OSError, UnicodeDecodeError):
                continue
    if near_limit_files:
        details["near_limit"] = {"pass": False, "files": near_limit_files}
        checks.append(False)
    else:
        checks.append(True)

    # 3. 未使用 Skill（usage.jsonl ベース）
    if data_dir is None:
        data_dir = Path.home() / ".claude" / "rl-anything"
    usage_file = data_dir / "usage.jsonl"
    if usage_file.exists():
        skill_names = set()
        for path in artifacts.get("skills", []):
            skill_names.add(path.parent.name)

        used_skills = _get_used_skills(usage_file, THRESHOLDS["unused_skill_days"])
        unused = [s for s in skill_names if s not in used_skills]
        if unused:
            details["unused_skills"] = {"pass": False, "skills": unused, "skipped": False}
            checks.append(False)
        else:
            checks.append(True)
    else:
        details["unused_skills"]["skipped"] = True
        # skip: スコアに含めない

    score = sum(1 for c in checks if c) / len(checks) if checks else 1.0
    return round(score, 4), details


def _get_used_skills(usage_file: Path, days: int) -> set:
    """usage.jsonl から直近 N 日間で使用されたスキル名を返す。"""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    used = set()
    try:
        with open(usage_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = record.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt < cutoff:
                        continue
                except (ValueError, AttributeError):
                    continue
                skill = record.get("skill", "")
                if skill:
                    used.add(skill)
    except (OSError, UnicodeDecodeError):
        pass
    return used


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
