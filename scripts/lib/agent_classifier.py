"""Agent 分類モジュール。

組み込み / カスタム (global/project) Agent を判定する共通ロジック。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Claude Code 組み込み Agent 名（Agent: プレフィックスなし）
BUILTIN_AGENT_NAMES: set[str] = {"Explore", "Plan", "general-purpose"}

AgentType = Literal["builtin", "custom_global", "custom_project"]


def classify_agent_type(
    agent_name: str,
    *,
    project_root: Path | None = None,
) -> AgentType:
    """Agent 名を組み込み / カスタム (global/project) に分類する。

    判定順:
    1. BUILTIN_AGENT_NAMES に含まれる → "builtin"
    2. .claude/agents/<name>.md (project) に存在 → "custom_project"（project 優先）
    3. ~/.claude/agents/<name>.md (global) に存在 → "custom_global"
    4. いずれにも該当しない → "builtin"（安全側フォールバック）

    ディレクトリ不在・I/O エラー時は WARNING ログを出力しスキップする。
    """
    if agent_name in BUILTIN_AGENT_NAMES:
        return "builtin"

    # Project agents directory
    if project_root is not None:
        project_agents_dir = project_root / ".claude" / "agents"
        if _agent_exists_in_dir(project_agents_dir, agent_name):
            return "custom_project"

    # Global agents directory
    global_agents_dir = Path.home() / ".claude" / "agents"
    if _agent_exists_in_dir(global_agents_dir, agent_name):
        return "custom_global"

    # Unknown agent → builtin fallback
    return "builtin"


def _agent_exists_in_dir(agents_dir: Path, agent_name: str) -> bool:
    """指定ディレクトリに Agent 定義ファイルが存在するか確認する。"""
    try:
        if not agents_dir.is_dir():
            return False
        return (agents_dir / f"{agent_name}.md").is_file()
    except OSError as e:
        logger.warning("Agent directory scan failed for %s: %s", agents_dir, e)
        return False
