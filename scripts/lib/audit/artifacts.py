"""アーティファクト一覧 + 行数制限チェック。

audit パッケージから切り出された Artifacts モジュール。
- find_artifacts: プロジェクト/グローバルから skills / rules / memory / CLAUDE.md を収集
- check_line_limits: 各種ファイルの行数 + バイト数上限チェック
"""
from pathlib import Path
from typing import Any, Dict, List

from frontmatter import count_content_lines

from ._constants import LIMITS
from .classification import classify_artifact_origin


def find_artifacts(project_dir: Path) -> Dict[str, List[Path]]:
    """プロジェクト内のアーティファクトを一覧する。"""
    result: Dict[str, List[Path]] = {
        "skills": [],
        "rules": [],
        "memory": [],
        "claude_md": [],
    }

    claude_dir = project_dir / ".claude"

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        result["claude_md"].append(claude_md)

    # Skills (.archive/ 配下はアーカイブ済みのため除外)
    skills_dir = claude_dir / "skills"
    if skills_dir.exists():
        for skill_md in skills_dir.rglob("SKILL.md"):
            if ".archive" not in skill_md.parts:
                result["skills"].append(skill_md)

    # Rules
    rules_dir = claude_dir / "rules"
    if rules_dir.exists():
        for rule_file in rules_dir.glob("*.md"):
            result["rules"].append(rule_file)

    # Memory
    memory_dir = claude_dir / "memory"
    if memory_dir.exists():
        for mem_file in memory_dir.glob("*.md"):
            result["memory"].append(mem_file)

    # Global artifacts
    global_claude = Path.home() / ".claude"
    global_skills = global_claude / "skills"
    if global_skills.exists():
        for skill_md in global_skills.rglob("SKILL.md"):
            if ".archive" not in skill_md.parts:
                result["skills"].append(skill_md)

    global_rules = global_claude / "rules"
    if global_rules.exists():
        for rule_file in global_rules.glob("*.md"):
            result["rules"].append(rule_file)

    return result


def check_line_limits(artifacts: Dict[str, List[Path]]) -> List[Dict[str, Any]]:
    """行数制限の超過を検出する。

    CLAUDE.md は violation ではなく warning のみ（collect_issues で除外）。
    """
    violations = []

    for path in artifacts.get("claude_md", []):
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines > LIMITS["CLAUDE.md"]:
            violations.append({"file": str(path), "lines": lines, "limit": LIMITS["CLAUDE.md"], "warning_only": True})

    for path in artifacts.get("rules", []):
        content = path.read_text(encoding="utf-8")
        lines = count_content_lines(content)
        # グローバル/プロジェクトルールで制限値を分ける
        home_str = str(Path.home())
        is_global = str(path).startswith(home_str) and ".claude/rules/" in str(path)
        limit = LIMITS["rules"] if is_global else LIMITS["project_rules"]
        if lines > limit:
            violations.append({"file": str(path), "lines": lines, "limit": limit})

    for path in artifacts.get("skills", []):
        # custom (プロジェクトローカル) のみ行数制限対象。plugin / global は
        # ダウンロード品なので除外する（ユーザーが管理するファイルではない）。
        if classify_artifact_origin(path) != "custom":
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines > LIMITS["SKILL.md"]:
            violations.append({"file": str(path), "lines": lines, "limit": LIMITS["SKILL.md"]})

    for path in artifacts.get("memory", []):
        content = path.read_text(encoding="utf-8")
        lines = content.count("\n") + 1
        limit = LIMITS["MEMORY.md"] if path.name == "MEMORY.md" else LIMITS["memory"]
        if lines > limit:
            violations.append({"file": str(path), "lines": lines, "limit": limit})
        # MEMORY.md のみバイトサイズチェック（CC v2.1.83 で 25KB 切り詰め追加）
        if path.name == "MEMORY.md":
            from lib.line_limit import MEMORY_MAX_BYTES, MEMORY_NEAR_LIMIT_BYTES

            byte_size = len(content.encode("utf-8"))
            if byte_size > MEMORY_MAX_BYTES:
                violations.append({"file": str(path), "bytes": byte_size, "bytes_limit": MEMORY_MAX_BYTES})
            elif byte_size > MEMORY_NEAR_LIMIT_BYTES:
                violations.append({"file": str(path), "bytes": byte_size, "bytes_limit": MEMORY_MAX_BYTES, "near_limit": True, "warning_only": True})

    return violations


def check_python_source_budgets(project_dir: Path) -> List[Dict[str, Any]]:
    """`scripts/**.py` / `hooks/**.py` の Python source 行数バジェット違反を検出する。

    audit.py が 2046 行まで肥大化した反省から、500 行で warn、800 行で violation。
    `__init__.py` / `conftest.py` 等の集約・テストファイルは除外
    （PYTHON_SOURCE_BUDGET_EXCLUDE_BASENAMES）。

    Returns:
        violation dict のリスト。`hard=True` は要分割（issues に積まれる）、
        `warning_only=True` は分割検討推奨（near-limit）。
    """
    from lib.line_limit import (
        MAX_PYTHON_SOURCE_LINES,
        MAX_PYTHON_SOURCE_HARD,
        PYTHON_SOURCE_BUDGET_EXCLUDE_BASENAMES,
    )

    violations: List[Dict[str, Any]] = []
    target_dirs = ["scripts", "hooks"]
    for sub in target_dirs:
        base = project_dir / sub
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if py.name in PYTHON_SOURCE_BUDGET_EXCLUDE_BASENAMES:
                continue
            # tests/ 配下は除外（fixture や e2e で長大化することがある）
            if "/tests/" in str(py):
                continue
            try:
                lines = py.read_text(encoding="utf-8").count("\n") + 1
            except (OSError, UnicodeDecodeError):
                continue
            if lines > MAX_PYTHON_SOURCE_HARD:
                violations.append({
                    "file": str(py),
                    "lines": lines,
                    "limit": MAX_PYTHON_SOURCE_HARD,
                    "hard": True,
                    "kind": "python_source_budget",
                })
            elif lines > MAX_PYTHON_SOURCE_LINES:
                violations.append({
                    "file": str(py),
                    "lines": lines,
                    "limit": MAX_PYTHON_SOURCE_LINES,
                    "warning_only": True,
                    "kind": "python_source_budget",
                })
    return violations
