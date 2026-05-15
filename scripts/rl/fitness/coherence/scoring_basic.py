#!/usr/bin/env python3
"""Coherence Score の Coverage / Consistency 軸スコアリング。

`scripts/rl/fitness/coherence/__init__.py` から切り出された
基礎軸 (Coverage / Consistency) のスコア計算 (Phase 10 / Slice 2)。
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .artifacts import _ensure_paths, _find_project_artifacts

# Coverage チェック項目
_COVERAGE_ITEMS = [
    "claude_md",
    "rules",
    "skills",
    "memory",
    "hooks",
    "skills_section",
]


# ---------- Coverage ----------

def score_coverage(project_dir: Path) -> Tuple[float, Dict[str, Any]]:
    """環境の各レイヤーが最低限存在するかチェックする。

    Returns:
        (score, details) — score は 0.0〜1.0、details は各項目の pass/fail。
    """
    artifacts = _find_project_artifacts(project_dir)

    if not artifacts["claude_dir_exists"] and not artifacts["claude_md"]:
        return 0.0, {item: False for item in _COVERAGE_ITEMS}

    checks = {
        "claude_md": artifacts["claude_md"] is not None,
        "rules": len(artifacts["rules"]) > 0,
        "skills": len(artifacts["skills"]) > 0,
        "memory": len(artifacts["memory"]) > 0,
        "hooks": artifacts["hooks"],
        "skills_section": artifacts["skills_section"],
    }

    passed = sum(1 for v in checks.values() if v)
    score = passed / len(checks)
    return round(score, 4), checks


# ---------- Consistency ----------

def score_consistency(project_dir: Path) -> Tuple[float, Dict[str, Any]]:
    """レイヤー間の矛盾や断絶がないかチェックする。

    チェック項目:
    1. CLAUDE.md で言及された Skill の実在チェック
    2. MEMORY.md 内のファイルパス参照の実在チェック
    3. トリガーワード重複チェック

    Returns:
        (score, details)
    """
    details: Dict[str, Any] = {
        "skill_existence": {"pass": True, "missing": []},
        "memory_paths": {"pass": True, "stale": []},
        "trigger_duplicates": {"pass": True, "duplicates": []},
    }

    checks_total = 0
    checks_passed = 0

    # 1. CLAUDE.md で言及された Skill の実在チェック
    claude_md = project_dir / "CLAUDE.md"
    skills_dir = project_dir / ".claude" / "skills"
    if claude_md.exists():
        checks_total += 1
        mentioned = _extract_mentioned_skills(claude_md)
        missing = []
        for skill_name in mentioned:
            skill_path = skills_dir / skill_name
            if not skill_path.exists():
                # SKILL.md があるサブディレクトリを確認
                if not (skill_path / "SKILL.md").exists() and not skill_path.is_dir():
                    missing.append(skill_name)
        if missing:
            details["skill_existence"] = {"pass": False, "missing": missing}
        else:
            checks_passed += 1

    # 2. MEMORY.md 内のファイルパス参照の実在チェック
    memory_dir = project_dir / ".claude" / "memory"
    if memory_dir.exists():
        memory_md = memory_dir / "MEMORY.md"
        # 他の memory ファイルも含む
        memory_files = list(memory_dir.glob("*.md"))
        stale_paths: List[str] = []
        for mf in memory_files:
            try:
                content = mf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            stale_paths.extend(_check_memory_paths(content, project_dir))

        if memory_files:
            checks_total += 1
            if stale_paths:
                details["memory_paths"] = {"pass": False, "stale": stale_paths}
            else:
                checks_passed += 1

    # 3. トリガーワード重複チェック
    _ensure_paths()
    from skill_triggers import extract_skill_triggers
    triggers_data = extract_skill_triggers(project_root=project_dir)
    if triggers_data:
        checks_total += 1
        trigger_map: Dict[str, List[str]] = {}
        for entry in triggers_data:
            for trigger in entry["triggers"]:
                t_lower = trigger.lower()
                if t_lower not in trigger_map:
                    trigger_map[t_lower] = []
                trigger_map[t_lower].append(entry["skill"])
        duplicates = {
            t: skills for t, skills in trigger_map.items() if len(skills) > 1
        }
        if duplicates:
            details["trigger_duplicates"] = {
                "pass": False,
                "duplicates": [
                    {"trigger": t, "skills": s} for t, s in duplicates.items()
                ],
            }
        else:
            checks_passed += 1

    score = checks_passed / checks_total if checks_total > 0 else 1.0
    return round(score, 4), details


def _extract_mentioned_skills(claude_md: Path) -> List[str]:
    """CLAUDE.md の Skills セクションから言及されたスキル名を抽出する。"""
    try:
        content = claude_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = content.splitlines()
    in_skills = False
    skills = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^#{1,3}\s+.*[Ss]kills?\b", stripped) or re.match(r"^#{1,3}\s+.*スキル", stripped):
            in_skills = True
            continue
        if in_skills and re.match(r"^#{1,3}\s+", stripped) and not re.match(
            r"^#{1,3}\s+.*[Ss]kills?\b", stripped
        ) and not re.match(r"^#{1,3}\s+.*スキル", stripped):
            break
        if not in_skills:
            continue
        # `- skill-name: ...` or `- /plugin:skill-name: ...`
        m = re.match(r"^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]", stripped)
        if m:
            name = m.group(1)
            # plugin:skill → skill
            if ":" in name:
                name = name.split(":", 1)[1]
            skills.append(name)
    return skills


_PATH_PATTERN = re.compile(
    r"(?:^|\s)([a-zA-Z_.][a-zA-Z0-9_./\-]*(?:\.(?:py|md|json|jsonl|yaml|yml|toml|sh|ts|js)|/))"
)


def _check_memory_paths(content: str, project_dir: Path) -> List[str]:
    """MEMORY.md 内のファイルパス参照が実在するかチェックする。"""
    stale = []
    in_code_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in _PATH_PATTERN.finditer(line):
            path_str = m.group(1).rstrip("/")
            # 短すぎるパスやよくある誤検出を除外
            if len(path_str) < 5 or path_str.startswith("http"):
                continue
            # セグメントが2つ以上あるパスのみ（e.g. scripts/lib/foo.py）
            if "/" not in path_str:
                continue
            check = project_dir / path_str
            if not check.exists():
                stale.append(path_str)
    return stale
