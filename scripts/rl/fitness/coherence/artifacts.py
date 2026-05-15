#!/usr/bin/env python3
"""Coherence Score 用のアーティファクト探索ヘルパー。

`scripts/rl/fitness/coherence/__init__.py` から切り出された
パス追加 + プロジェクト構造検出ロジック (Phase 10 / Slice 1)。
"""
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

# scripts/rl/fitness/coherence/artifacts.py → plugin root は 5 つ上
_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent


def _ensure_paths():
    """遅延パス追加。テスト時のパス衝突を防ぐ。"""
    paths = [
        str(_plugin_root / "scripts" / "lib"),
        str(_plugin_root / "scripts"),
        str(_plugin_root / "skills" / "audit" / "scripts"),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


def _is_plugin_project(project_dir: Path) -> bool:
    """`.claude-plugin/plugin.json` が存在する場合、プラグインプロジェクトと判定する。"""
    return (project_dir / ".claude-plugin" / "plugin.json").exists()


def _find_project_artifacts(project_dir: Path) -> Dict[str, Any]:
    """プロジェクト内のアーティファクトを探索する。

    `.claude-plugin/plugin.json` が存在する場合はプラグイン構造として扱い、
    プロジェクトルートの `skills/`・`hooks/` ディレクトリを検索する。
    """
    claude_dir = project_dir / ".claude"
    result: Dict[str, Any] = {
        "claude_md": None,
        "rules": [],
        "skills": [],
        "memory": [],
        "hooks": False,
        "skills_section": False,
        "claude_dir_exists": claude_dir.exists(),
    }

    is_plugin = _is_plugin_project(project_dir)

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        result["claude_md"] = claude_md

    # Rules
    rules_dir = claude_dir / "rules"
    if rules_dir.exists():
        result["rules"] = list(rules_dir.glob("*.md"))

    # Skills: プラグイン構造ではプロジェクトルートの skills/ を優先
    if is_plugin:
        plugin_skills_dir = project_dir / "skills"
        if plugin_skills_dir.exists():
            result["skills"] = list(plugin_skills_dir.rglob("SKILL.md"))
    if not result["skills"]:
        skills_dir = claude_dir / "skills"
        if skills_dir.exists():
            result["skills"] = list(skills_dir.rglob("SKILL.md"))

    # Memory: プラグイン構造では agent-memory/ または MEMORY.md も確認
    memory_dir = claude_dir / "memory"
    if memory_dir.exists():
        result["memory"] = list(memory_dir.glob("*.md"))
    if not result["memory"] and is_plugin:
        agent_memory_dir = claude_dir / "agent-memory"
        if agent_memory_dir.exists():
            result["memory"] = list(agent_memory_dir.glob("*.md"))
        if not result["memory"]:
            root_memory = project_dir / "MEMORY.md"
            if root_memory.exists():
                result["memory"] = [root_memory]
    # グローバル auto-memory フォールバック: ~/.claude/projects/<encoded-path>/memory/
    if not result["memory"]:
        resolved = str(project_dir.resolve())
        encoded = resolved.replace("/", "-")
        home = Path.home()
        for candidate in [encoded, encoded.lstrip("-")]:
            auto_mem_dir = home / ".claude" / "projects" / candidate / "memory"
            if auto_mem_dir.is_dir():
                result["memory"] = list(auto_mem_dir.glob("*.md"))
                break

    # Hooks: settings.json の hooks 設定、またはプラグイン構造では hooks/ ディレクトリ
    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            result["hooks"] = bool(settings.get("hooks"))
        except (json.JSONDecodeError, OSError):
            pass
    if not result["hooks"] and is_plugin:
        hooks_dir = project_dir / "hooks"
        if hooks_dir.exists():
            hook_files = [
                f for f in hooks_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            result["hooks"] = len(hook_files) > 0

    # CLAUDE.md に Skills セクションがあるか
    if result["claude_md"]:
        try:
            content = result["claude_md"].read_text(encoding="utf-8")
            result["skills_section"] = bool(
                re.search(r"^#{1,3}\s+.*[Ss]kills?\b|^#{1,3}\s+.*スキル", content, re.MULTILINE)
            )
        except (OSError, UnicodeDecodeError):
            pass

    return result


def _find_artifacts_local(project_dir: Path) -> Dict[str, List[Path]]:
    """プロジェクト限定のアーティファクト探索（audit互換形式、グローバル除外）。"""
    claude_dir = project_dir / ".claude"
    result: Dict[str, List[Path]] = {
        "skills": [],
        "rules": [],
        "memory": [],
        "claude_md": [],
    }
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        result["claude_md"].append(claude_md)
    skills_dir = claude_dir / "skills"
    if skills_dir.exists():
        result["skills"] = list(skills_dir.rglob("SKILL.md"))
    rules_dir = claude_dir / "rules"
    if rules_dir.exists():
        result["rules"] = list(rules_dir.glob("*.md"))
    # ローカル .claude/memory/
    memory_dir = claude_dir / "memory"
    if memory_dir.exists():
        result["memory"] = list(memory_dir.glob("*.md"))
    # グローバル auto-memory: ~/.claude/projects/<encoded-path>/memory/
    if not result["memory"]:
        resolved = str(project_dir.resolve())
        encoded = resolved.replace("/", "-")
        home = Path.home()
        for candidate in [encoded, encoded.lstrip("-")]:
            auto_mem_dir = home / ".claude" / "projects" / candidate / "memory"
            if auto_mem_dir.is_dir():
                result["memory"] = list(auto_mem_dir.glob("*.md"))
                break
    return result
