"""エージェント品質診断モジュール。

~/.claude/agents/ およびプロジェクト固有 agents/ のエージェント定義を
走査・品質チェック・アンチパターン検出・ベストプラクティス照合する。

agency-agents (https://github.com/msitarzewski/agency-agents) のパターンを
参照カタログとして使用。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from frontmatter import count_content_lines, parse_frontmatter

logger = logging.getLogger(__name__)

# --- Constants ---

UPSTREAM_REPO = "msitarzewski/agency-agents"

# 行数閾値
BLOAT_LINE_THRESHOLD = 400
KITCHEN_SINK_HEADING_THRESHOLD = 12

# 曖昧表現キーワード（日英）
VAGUE_KEYWORDS = [
    "anything",
    "everything",
    "whatever",
    "flexible",
    "versatile",
    "any task",
    "なんでも",
    "柔軟に",
    "何でも",
    "すべて対応",
    "あらゆる",
]
VAGUE_KEYWORD_THRESHOLD = 3

# description 品質閾値
MIN_DESCRIPTION_LENGTH = 30  # 文字数
# 出力指示の検出パターン（セクション見出しではなく本文中の行動指示）
OUTPUT_SPEC_PATTERNS = [
    r"(?i)provide\s+.*(feedback|output|report|summary|results)",
    r"(?i)return\s+.*(result|output|summary|report|list)",
    r"(?i)format\s+.*(as|using|with|into)",
    r"(?i)include\s+.*(specific|concrete|actionable)",
    r"(?i)organized?\s+by",
    r"(?i)(出力|返す|提示|レポート|報告).*形式",
    r"(?i)##\s*(deliverables?|output|成果物|出力)",
    r"```",  # コードブロック = 出力テンプレートの可能性
    r"(?i)respond\s+with",
    r"(?i)generate\s+.*(report|summary|plan|list)",
    r"(?i)(結果|アクション|プラン)を(提示|出力|表示)",
]
OUTPUT_SPEC_MIN_MATCHES = 2

# 知識ハードコード検出パターン（バージョン番号・具体パス・プロジェクト固有名詞リスト）
KNOWLEDGE_HARDCODING_PATTERNS = [
    r"\b(?:v\d+\.\d+|\(\d+[-–]\d+\))",  # バージョン範囲: (13-15), v1.2.3
    r"(?:~\/|https?:\/\/[^\s)]{10,}|\/[a-z][-a-z0-9/]{4,}(?:\.py|\.ts|\.md|\.json))",  # パス/URL
    r"^\s*[-*]\s*\*\*[A-Za-z][-A-Za-z0-9_]+\*\*\s*:",  # **projectName**: 形式の固有名詞リスト
]
KNOWLEDGE_HARDCODING_LOW_THRESHOLD = 3
KNOWLEDGE_HARDCODING_MEDIUM_THRESHOLD = 10

# JIT識別子戦略の検出パターン
JIT_PATTERNS = [
    r"(?i)(read|grep|bash|確認|参照).*(before|前に|必ず|always)",
    r"(?i)(ファイルを|file).*(確認|read|check)",
    r"(?i)dynamic\s*knowledge",
    r"(?i)jit|just.in.time",
    r"(?i)記憶に頼らず",
    r"(?i)実行時に.*確認",
]

# --- Anti-pattern catalog ---

ANTI_PATTERNS = {
    "missing_frontmatter": {
        "description": "YAML frontmatter (name, description) が欠落",
        "severity": "high",
    },
    "vague_mission": {
        "description": "曖昧な表現が多く、専門性が不明確",
        "severity": "medium",
    },
    "weak_output_spec": {
        "description": "出力形式・成果物の指示が本文中にない",
        "severity": "medium",
    },
    "weak_trigger_description": {
        "description": "description が短すぎるか曖昧で、委譲判断に不十分",
        "severity": "medium",
    },
    "missing_tools_restriction": {
        "description": "tools フィールド未設定（全ツール継承 → スコープ過大）",
        "severity": "low",
    },
    "no_boundaries": {
        "description": "責任範囲が不明確（何をしないかが書かれていない）",
        "severity": "low",
    },
    "kitchen_sink": {
        "description": "1つのエージェントに過剰な責任（セクション数が多すぎる）",
        "severity": "medium",
    },
    "no_checklist": {
        "description": "手順やチェックリストが定義されていない",
        "severity": "low",
    },
    "bloated_agent": {
        "description": f"定義が {BLOAT_LINE_THRESHOLD} 行を超えて肥大化",
        "severity": "medium",
    },
    "knowledge_hardcoding": {
        "description": "バージョン番号・具体パス・プロジェクト固有名詞をハードコード（陳腐化リスク）",
        "severity": "low",
    },
}

# --- Best practice catalog (agency-agents patterns) ---

BEST_PRACTICES = {
    "structured_identity": {
        "description": "Identity / Role / Personality セクションで自己定義",
        "detect_patterns": [
            r"(?i)##\s*(your\s+)?identity",
            r"(?i)##\s*(your\s+)?role",
            r"(?i)##\s*personality",
            r"(?i)##\s*あなたの(役割|アイデンティティ)",
        ],
    },
    "success_metrics": {
        "description": "測定可能な成功基準の定義",
        "detect_patterns": [
            r"(?i)##\s*success\s*(metrics|criteria)",
            r"(?i)##\s*成功(基準|指標)",
            r"(?i)##\s*KPI",
        ],
    },
    "communication_style": {
        "description": "コミュニケーションスタイルの明示",
        "detect_patterns": [
            r"(?i)##\s*communication\s*style",
            r"(?i)##\s*コミュニケーション",
            r"(?i)##\s*(tone|voice)",
        ],
    },
    "critical_rules": {
        "description": "絶対守るべきルールの明示",
        "detect_patterns": [
            r"(?i)##\s*critical\s*rules",
            r"(?i)##\s*重要な?ルール",
            r"(?i)##\s*rules",
            r"(?i)##\s*constraints",
        ],
    },
    "deliverable_templates": {
        "description": "具体的な成果物テンプレート / 出力形式の定義",
        "detect_patterns": [
            r"(?i)##\s*deliverables?",
            r"(?i)##\s*output",
            r"(?i)##\s*成果物",
            r"(?i)##\s*出力",
            r"```",  # コードブロックがあれば出力例がある可能性
        ],
    },
    "priority_markers": {
        "description": "優先度マーカー（🔴🟡💭 等）による分類",
        "detect_patterns": [
            r"🔴|🟡|💭|🟢",
            r"(?i)\*\*(blocker|critical|suggestion|nit)\*\*",
            r"(?i)(P0|P1|P2|P3)",
        ],
    },
    "jit_file_references": {
        "description": "JIT識別子戦略：回答前にファイルを動的確認する鉄則の明示",
        "detect_patterns": JIT_PATTERNS,
    },
}


# --- Data structures ---


@dataclass
class AgentInfo:
    """エージェント定義の情報。"""

    name: str
    path: Path
    scope: str  # "global" | "project"
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    content: str = ""
    line_count: int = 0


# --- Core functions ---


def scan_agents(
    *,
    project_root: Optional[Path] = None,
) -> List[AgentInfo]:
    """グローバルおよびプロジェクト固有のエージェント定義を走査する。

    同名エージェントが global と project にある場合は project 優先。

    Args:
        project_root: プロジェクトルートパス（省略時はグローバルのみ）

    Returns:
        AgentInfo のリスト
    """
    agents: Dict[str, AgentInfo] = {}

    # Global agents
    global_dir = Path.home() / ".claude" / "agents"
    _scan_dir(global_dir, "global", agents)

    # Project agents (project 優先で上書き)
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
    """エージェント定義の品質を診断する。

    Args:
        agent: AgentInfo

    Returns:
        {"agent": str, "path": str, "scope": str, "score": float,
         "issues": List[dict], "suggestions": List[dict]}
    """
    # content/frontmatter が未設定の場合はファイルから読み直す
    if not agent.content and agent.path.exists():
        agent.content = agent.path.read_text(encoding="utf-8")
        agent.frontmatter = parse_frontmatter(agent.path)
        agent.line_count = count_content_lines(agent.content)

    issues: List[Dict[str, Any]] = []
    suggestions: List[Dict[str, Any]] = []
    score = 1.0

    # 1. Frontmatter check
    if not agent.frontmatter or not agent.frontmatter.get("name"):
        issues.append({
            "type": "missing_frontmatter",
            "detail": ANTI_PATTERNS["missing_frontmatter"]["description"],
            "severity": "high",
        })
        score -= 0.3

    # 2. Vague mission check
    vague_count = _count_vague_keywords(agent.content)
    if vague_count >= VAGUE_KEYWORD_THRESHOLD:
        issues.append({
            "type": "vague_mission",
            "detail": f"曖昧表現が {vague_count} 個検出",
            "severity": "medium",
        })
        score -= 0.15

    # 3. Output spec check (本文中の出力指示を検出 — セクション見出しだけでなく)
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

    # 4. Trigger description check (description の品質)
    desc = agent.frontmatter.get("description", "") if agent.frontmatter else ""
    if isinstance(desc, str) and len(desc.strip()) < MIN_DESCRIPTION_LENGTH:
        issues.append({
            "type": "weak_trigger_description",
            "detail": f"description が {len(desc.strip())} 文字 (最低 {MIN_DESCRIPTION_LENGTH})",
            "severity": "medium",
        })
        score -= 0.1

    # 5. Tools restriction check (tools フィールド未設定)
    if agent.frontmatter and not agent.frontmatter.get("tools"):
        issues.append({
            "type": "missing_tools_restriction",
            "detail": ANTI_PATTERNS["missing_tools_restriction"]["description"],
            "severity": "low",
        })
        score -= 0.05

    # 6. Checklist check
    if not _has_checklist(agent.content):
        issues.append({
            "type": "no_checklist",
            "detail": ANTI_PATTERNS["no_checklist"]["description"],
            "severity": "low",
        })
        score -= 0.05

    # 7. Kitchen sink check
    heading_count = len(re.findall(r"^##\s+", agent.content, re.MULTILINE))
    if heading_count >= KITCHEN_SINK_HEADING_THRESHOLD:
        issues.append({
            "type": "kitchen_sink",
            "detail": f"セクション数 {heading_count} (閾値 {KITCHEN_SINK_HEADING_THRESHOLD})",
            "severity": "medium",
        })
        score -= 0.1

    # 8. Bloat check
    if agent.line_count > BLOAT_LINE_THRESHOLD:
        issues.append({
            "type": "bloated_agent",
            "detail": f"{agent.line_count} 行 (閾値 {BLOAT_LINE_THRESHOLD})",
            "severity": "medium",
        })
        score -= 0.1

    # 8b. Knowledge hardcoding check
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

    # 9. Best practice suggestions
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


# --- Upstream monitoring ---


def check_upstream(
    *,
    state_file: Optional[Path] = None,
    repo: str = UPSTREAM_REPO,
) -> Dict[str, Any]:
    """agency-agents リポジトリの更新をチェックする。

    前回チェック時のコミットハッシュと比較し、更新有無を返す。
    gh api 失敗時は graceful に skip する。

    Args:
        state_file: 状態保存ファイルのパス
        repo: チェック対象リポジトリ

    Returns:
        {"status": "first_check"|"no_update"|"updated"|"error", ...}
    """
    current_hash = _fetch_latest_commit_hash(repo)
    if current_hash is None:
        return {
            "status": "error",
            "message": f"Failed to fetch latest commit from {repo}",
        }

    # 前回の状態を読み込み
    stored_hash = None
    if state_file is not None and state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            stored_hash = state.get("upstream_commit_hash")
        except (json.JSONDecodeError, OSError):
            pass

    # 状態を保存
    if state_file is not None:
        try:
            existing_state = {}
            if state_file.exists():
                try:
                    existing_state = json.loads(
                        state_file.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    pass
            existing_state["upstream_commit_hash"] = current_hash
            state_file.write_text(
                json.dumps(existing_state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to save state: %s", e)

    if stored_hash is None:
        return {
            "status": "first_check",
            "current_hash": current_hash,
            "repo": repo,
        }

    if stored_hash == current_hash:
        return {
            "status": "no_update",
            "current_hash": current_hash,
            "repo": repo,
        }

    return {
        "status": "updated",
        "previous_hash": stored_hash,
        "current_hash": current_hash,
        "repo": repo,
    }


def _fetch_latest_commit_hash(repo: str) -> Optional[str]:
    """gh api でリポジトリの最新コミットハッシュを取得する。

    失敗時は None を返す（graceful degradation）。
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/commits?per_page=1",
                "--jq",
                ".[0].sha",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


# --- Internal helpers ---


def _count_vague_keywords(content: str) -> int:
    """曖昧表現キーワードの出現数をカウントする。"""
    lower = content.lower()
    return sum(1 for kw in VAGUE_KEYWORDS if kw.lower() in lower)


def _has_section(content: str, patterns: List[str]) -> bool:
    """いずれかのパターンにマッチするセクションがあるか。"""
    return any(re.search(p, content) for p in patterns)


def _has_checklist(content: str) -> bool:
    """チェックリストまたは番号付きリストが含まれているか。"""
    # Markdown チェックリスト
    if re.search(r"- \[[ x]\]", content):
        return True
    # 番号付きリスト（3つ以上連続）
    numbered = re.findall(r"^\d+\.\s+", content, re.MULTILINE)
    if len(numbered) >= 3:
        return True
    # チェックリストセクション
    if re.search(r"(?i)##\s*(checklist|チェックリスト|手順)", content):
        return True
    return False
