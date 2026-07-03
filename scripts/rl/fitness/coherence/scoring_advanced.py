#!/usr/bin/env python3
"""Coherence Score の Completeness / Efficiency 軸スコアリング。

`scripts/rl/fitness/coherence/__init__.py` から切り出された
高度な軸 (Completeness / Efficiency) のスコア計算 (Phase 10 / Slice 3)。
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .artifacts import _ensure_paths, _find_project_artifacts, _find_artifacts_local


def _thresholds() -> Dict[str, Any]:
    """`coherence.THRESHOLDS` を遅延参照する。

    テスト等で `coherence.THRESHOLDS` を monkeypatch するケースに追従するため、
    `__init__.py` の THRESHOLDS を毎回参照する。
    """
    from . import THRESHOLDS  # noqa: WPS433 — lazy import で最新値を取得
    return THRESHOLDS


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
    THRESHOLDS = _thresholds()
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
    THRESHOLDS = _thresholds()
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
        data_dir = Path.home() / ".claude" / "evolve-anything"
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
    """usage.jsonl から直近 N 日間で使用されたスキル名（bare 形）を返す。

    usage.jsonl は skill_name/skill・ts/timestamp の 3 スキーマ混在（#139）。片側の
    フィールドだけを見ると常に空集合になり全 custom skill が永遠に「未使用」判定に倒れる
    ため、レコードパースは ``rl_common`` の単一ソースに委譲する。返すスキル名は plugin 修飾
    形（``<plugin>:<skill>``）を bare 名へ正規化して、比較先の SKILL.md dir 名と名前空間を
    揃える（#577/#578 の join-key 不一致対策）。``Agent:*`` は subagent 帰属でスキルでない
    ため除外する。
    """
    from datetime import datetime, timedelta, timezone

    _ensure_paths()
    from rl_common import usage_skill_name, usage_timestamp

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
                ts = usage_timestamp(record)
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt < cutoff:
                        continue
                except (ValueError, AttributeError):
                    continue
                bare = _bare_used_skill(usage_skill_name(record))
                if bare:
                    used.add(bare)
    except (OSError, UnicodeDecodeError):
        pass
    return used


def _bare_used_skill(skill: str) -> str:
    """起動時スキル名を bare 名（SKILL.md dir 名）へ正規化する。

    ``<plugin>:<skill>`` は最後の ``:`` 以降が skill 名（dir 名に ``:`` は含まれない）。
    ``Agent:*`` は subagent 帰属でありスキルでないため "" を返し join 対象から外す。
    audit.multiview_eval._bare_skill_name と同方式（#577）。
    """
    if not skill or skill.startswith("Agent:"):
        return ""
    return skill.rsplit(":", 1)[-1]
