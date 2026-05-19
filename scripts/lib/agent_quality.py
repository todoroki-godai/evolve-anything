"""エージェント品質診断モジュール。

~/.claude/agents/ およびプロジェクト固有 agents/ のエージェント定義を
走査・品質チェック・アンチパターン検出・ベストプラクティス照合する。

agency-agents (https://github.com/msitarzewski/agency-agents) のパターンを
参照カタログとして使用。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from frontmatter import count_content_lines, parse_frontmatter
from lib.agent_quality_catalog import (
    ANTI_PATTERNS,
    BEST_PRACTICES,
    BLOAT_LINE_THRESHOLD,
    KITCHEN_SINK_HEADING_THRESHOLD,
    KNOWLEDGE_HARDCODING_LOW_THRESHOLD,
    KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD,
    KNOWLEDGE_HARDCODING_PATTERNS,
    MIN_DESCRIPTION_LENGTH,
    OUTPUT_SPEC_MIN_MATCHES,
    OUTPUT_SPEC_PATTERNS,
    VAGUE_KEYWORD_THRESHOLD,
    VAGUE_KEYWORDS,
)
from lib.agent_quality_upstream import check_upstream  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """エージェント定義の情報。"""

    name: str
    path: Path
    scope: str  # "global" | "project"
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    content: str = ""
    line_count: int = 0


def scan_agents(
    *,
    project_root: Optional[Path] = None,
) -> List[AgentInfo]:
    """グローバルおよびプロジェクト固有のエージェント定義を走査する。

    同名エージェントが global と project にある場合は project 優先。
    """
    agents: Dict[str, AgentInfo] = {}

    global_dir = Path.home() / ".claude" / "agents"
    _scan_dir(global_dir, "global", agents)

    if project_root is not None:
        project_dir = project_root / ".claude" / "agents"
        _scan_dir(project_dir, "project", agents)

    return list(agents.values())


def _scan_dir(
    agents_dir: Path, scope: str, agents: Dict[str, AgentInfo]
) -> None:
    """指定ディレクトリのエージェント .md を走査する。"""
    try:
        if not agents_dir.is_dir():
            return
        for md_file in sorted(agents_dir.glob("*.md")):
            name = md_file.stem
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = parse_frontmatter(md_file)
                line_count = count_content_lines(content)
                agents[name] = AgentInfo(
                    name=name,
                    path=md_file,
                    scope=scope,
                    frontmatter=fm,
                    content=content,
                    line_count=line_count,
                )
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Failed to read agent %s: %s", md_file, e)
    except OSError as e:
        logger.warning("Failed to scan agents dir %s: %s", agents_dir, e)


def check_quality(agent: AgentInfo) -> Dict[str, Any]:
    """エージェント定義の品質を診断する。"""
    if not agent.content and agent.path.exists():
        agent.content = agent.path.read_text(encoding="utf-8")
        agent.frontmatter = parse_frontmatter(agent.path)
        agent.line_count = count_content_lines(agent.content)

    issues: List[Dict[str, Any]] = []
    suggestions: List[Dict[str, Any]] = []
    score = 1.0

    if not agent.frontmatter or not agent.frontmatter.get("name"):
        issues.append({
            "type": "missing_frontmatter",
            "detail": ANTI_PATTERNS["missing_frontmatter"]["description"],
            "severity": "high",
        })
        score -= 0.3

    vague_count = _count_vague_keywords(agent.content)
    if vague_count >= VAGUE_KEYWORD_THRESHOLD:
        issues.append({
            "type": "vague_mission",
            "detail": f"曖昧表現が {vague_count} 個検出",
            "severity": "medium",
        })
        score -= 0.15

    output_matches = sum(
        1 for p in OUTPUT_SPEC_PATTERNS if re.search(p, agent.content)
    )
    if output_matches < OUTPUT_SPEC_MIN_MATCHES:
        issues.append({
            "type": "weak_output_spec",
            "detail": f"出力形式の指示が不十分 (検出 {output_matches}/{OUTPUT_SPEC_MIN_MATCHES})",
            "severity": "medium",
        })
        score -= 0.1

    desc = agent.frontmatter.get("description", "") if agent.frontmatter else ""
    if isinstance(desc, str) and len(desc.strip()) < MIN_DESCRIPTION_LENGTH:
        issues.append({
            "type": "weak_trigger_description",
            "detail": f"description が {len(desc.strip())} 文字 (最低 {MIN_DESCRIPTION_LENGTH})",
            "severity": "medium",
        })
        score -= 0.1

    if agent.frontmatter and not agent.frontmatter.get("tools"):
        issues.append({
            "type": "missing_tools_restriction",
            "detail": ANTI_PATTERNS["missing_tools_restriction"]["description"],
            "severity": "low",
        })
        score -= 0.05

    if not _has_checklist(agent.content):
        issues.append({
            "type": "no_checklist",
            "detail": ANTI_PATTERNS["no_checklist"]["description"],
            "severity": "low",
        })
        score -= 0.05

    heading_count = len(re.findall(r"^##\s+", agent.content, re.MULTILINE))
    if heading_count >= KITCHEN_SINK_HEADING_THRESHOLD:
        issues.append({
            "type": "kitchen_sink",
            "detail": f"セクション数 {heading_count} (閾値 {KITCHEN_SINK_HEADING_THRESHOLD})",
            "severity": "medium",
        })
        score -= 0.1

    if agent.line_count > BLOAT_LINE_THRESHOLD:
        issues.append({
            "type": "bloated_agent",
            "detail": f"{agent.line_count} 行 (閾値 {BLOAT_LINE_THRESHOLD})",
            "severity": "medium",
        })
        score -= 0.1

    hc_matches = sum(
        len(re.findall(p, agent.content, re.MULTILINE))
        for p in KNOWLEDGE_HARDCODING_PATTERNS
    )
    if hc_matches >= KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD:
        issues.append({
            "type": "knowledge_hardcoding",
            "detail": f"ハードコード候補 {hc_matches} 箇所（閾値 {KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD}）。JIT識別子戦略を検討",
            "severity": "medium",
        })
        score -= 0.1
    elif hc_matches >= KNOWLEDGE_HARDCODING_LOW_THRESHOLD:
        issues.append({
            "type": "knowledge_hardcoding",
            "detail": f"ハードコード候補 {hc_matches} 箇所。意図的なら無視可。陳腐化リスクがある場合はJIT化を検討",
            "severity": "low",
        })
        score -= 0.05

    for bp_name, bp_info in BEST_PRACTICES.items():
        if not _has_section(agent.content, bp_info["detect_patterns"]):
            suggestions.append({
                "pattern": bp_name,
                "description": bp_info["description"],
            })

    score = max(0.0, min(1.0, score))

    return {
        "agent": agent.name,
        "path": str(agent.path),
        "scope": agent.scope,
        "score": round(score, 2),
        "issues": issues,
        "suggestions": suggestions,
        "line_count": agent.line_count,
    }


def _count_vague_keywords(content: str) -> int:
    lower = content.lower()
    return sum(1 for kw in VAGUE_KEYWORDS if kw.lower() in lower)


def _has_section(content: str, patterns: List[str]) -> bool:
    return any(re.search(p, content) for p in patterns)


def _has_checklist(content: str) -> bool:
    if re.search(r"- \[[ x]\]", content):
        return True
    numbered = re.findall(r"^\d+\.\s+", content, re.MULTILINE)
    if len(numbered) >= 3:
        return True
    if re.search(r"(?i)##\s*(checklist|チェックリスト|手順)", content):
        return True
    return False
