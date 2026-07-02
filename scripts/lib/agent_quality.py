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
from agent_quality_catalog import (
    ANTI_PATTERNS,
    BEST_PRACTICES,
    BLOAT_LINE_THRESHOLD,
    EXACT_MODEL_ID_PATTERN,
    KITCHEN_SINK_HEADING_THRESHOLD,
    KNOWLEDGE_HARDCODING_LOW_THRESHOLD,
    KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD,
    KNOWLEDGE_HARDCODING_PATTERNS,
    MIN_DESCRIPTION_LENGTH,
    MODEL_ALIASES,
    OUTPUT_SPEC_MIN_MATCHES,
    OUTPUT_SPEC_PATTERNS,
    SKILL_ANATOMY,
    VAGUE_KEYWORD_THRESHOLD,
    VAGUE_KEYWORDS,
    missing_anatomy_sections,
)
from agent_quality_upstream import check_upstream  # noqa: F401 — re-export

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

    pin_result = check_model_pin(agent)
    if pin_result["pinned"]:
        issues.append({
            "type": "exact_model_id_pin",
            "detail": (
                f"model: {pin_result['current_value']!r} は exact ID pin — "
                f"推奨エイリアス: {pin_result['recommended_alias']!r}"
            ),
            "severity": "medium",
        })
        score -= 0.1

    grant_result = check_tools_grant_divergence(agent)
    if grant_result["diverged"]:
        issues.append({
            "type": "tools_grant_divergence",
            "detail": TOOLS_GRANT_DIVERGENCE_ADVISORY,
            "severity": "low",
        })
        score -= 0.05

    for bp_name, bp_info in BEST_PRACTICES.items():
        if not _has_section(agent.content, bp_info["detect_patterns"]):
            suggestions.append({
                "pattern": bp_name,
                "description": bp_info["description"],
            })

    missing_anatomy = missing_anatomy_sections(agent.content)
    if missing_anatomy:
        labels = ", ".join(m["label"] for m in missing_anatomy)
        suggestions.append({
            "pattern": "skill_anatomy",
            "description": f'{SKILL_ANATOMY["description"]}（欠落: {labels}）',
            "source": SKILL_ANATOMY["source"],
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


def check_model_pin(agent: AgentInfo) -> Dict[str, Any]:
    """frontmatter の model: フィールドが exact model ID pin かを検査する。

    Returns:
        {
            "pinned": bool,
            "current_value": str | None,  # pin されている場合の現在値
            "file": str,                   # エージェントファイルパス
            "recommended_alias": str | None,  # 推奨エイリアス（pinned=True 時のみ）
        }
    """
    if not agent.frontmatter:
        if agent.path.exists():
            agent.frontmatter = parse_frontmatter(agent.path)
        else:
            return {"pinned": False, "current_value": None, "file": str(agent.path), "recommended_alias": None}

    model_value = agent.frontmatter.get("model")
    if model_value is None:
        return {"pinned": False, "current_value": None, "file": str(agent.path), "recommended_alias": None}

    model_str = str(model_value).strip()

    # エイリアス（完全一致）はスキップ
    if model_str.lower() in MODEL_ALIASES:
        return {"pinned": False, "current_value": model_str, "file": str(agent.path), "recommended_alias": None}

    # exact model ID パターン: claude- 始まりかつバージョン数字を含む
    if EXACT_MODEL_ID_PATTERN.match(model_str):
        recommended = _suggest_alias(model_str)
        return {
            "pinned": True,
            "current_value": model_str,
            "file": str(agent.path),
            "recommended_alias": recommended,
        }

    return {"pinned": False, "current_value": model_str, "file": str(agent.path), "recommended_alias": None}


def _suggest_alias(model_id: str) -> str:
    """exact model ID から推奨エイリアスを返す。

    例:
        claude-opus-4-8   -> "opus"
        claude-sonnet-4-6 -> "sonnet"
        claude-haiku-4-0  -> "haiku"
        claude-fable-1-0  -> "fable"
    """
    lower = model_id.lower()
    for alias in ("opus", "sonnet", "haiku", "fable"):
        if alias in lower:
            return alias
    return "sonnet"  # フォールバック


TOOLS_GRANT_DIVERGENCE_ADVISORY = (
    "memory: 宣言があるため実行時は Write/Edit が自動付与されます"
    "（tools 宣言と独立）。助言専用 agent なら本文にスコープ限定の安全弁"
    "（例: 『Write/Edit は Persistent Agent Memory への自己メモ更新のみ。"
    "成果物ファイルの作成・編集はしない』）を明記してください"
)


def _normalize_tools(tools_value: Any) -> List[str]:
    """frontmatter の tools: 値をツール名リストに正規化する。

    tools は文字列（"Read, Grep, Glob" のカンマ区切り）でも
    YAML リスト（["Read", "Grep"]）でも宣言され得るため両対応する。
    """
    if isinstance(tools_value, str):
        return [t.strip() for t in tools_value.split(",") if t.strip()]
    if isinstance(tools_value, (list, tuple)):
        return [str(t).strip() for t in tools_value if str(t).strip()]
    return []


def check_tools_grant_divergence(agent: AgentInfo) -> Dict[str, Any]:
    """tools 宣言と実付与の乖離を検出する（#130）。

    助言専用設計の agent（tools 宣言に Write/Edit なし）でも、frontmatter に
    `memory:` があると harness が Persistent Agent Memory 書込用に Write/Edit を
    自動付与する（tools 宣言と独立）。実行時付与は静的 audit から見えないが、
    「memory: あり + tools: に Write/Edit なし」という宣言ベースヒューリスティック
    で決定論検出できる。

    `tools:` 宣言自体が無い agent（全ツール継承）は乖離が存在しないため対象外。

    Returns:
        {
            "diverged": bool,
            "file": str,
            "declared_tools": List[str],  # 正規化済みツール名
            "has_memory": bool,
        }
    """
    if not agent.frontmatter:
        if agent.path.exists():
            agent.frontmatter = parse_frontmatter(agent.path)
        else:
            return {"diverged": False, "file": str(agent.path), "declared_tools": [], "has_memory": False}

    fm = agent.frontmatter or {}
    has_memory = "memory" in fm and fm.get("memory") is not None
    declared_tools = _normalize_tools(fm.get("tools"))

    # tools 宣言が無い（全ツール継承）→ 乖離なし（誤検知回避）
    if not declared_tools:
        return {"diverged": False, "file": str(agent.path), "declared_tools": [], "has_memory": has_memory}

    has_write_or_edit = any(t in ("Write", "Edit") for t in declared_tools)
    diverged = has_memory and not has_write_or_edit

    return {
        "diverged": diverged,
        "file": str(agent.path),
        "declared_tools": declared_tools,
        "has_memory": has_memory,
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
