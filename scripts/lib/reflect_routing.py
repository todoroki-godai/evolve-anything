"""correction ルーティング・検出ロジック。"""
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from lib.frontmatter import parse_frontmatter as _parse_rule_frontmatter  # noqa: F401
from lib.path_extractor import extract_paths_outside_codeblocks
from lib.skill_origin import (
    is_protected_skill,
    suggest_local_alternative,
)
from lib.skill_triggers import extract_skill_triggers

# Auto-memory トピック分類キーワード
_AUTO_MEMORY_TOPICS = {
    "model-preferences": ["gpt-", "claude-", "gemini-", "o3", "o4", "model", "llm"],
    "tool-usage": ["mcp", "tool", "plugin", "api", "endpoint"],
    "coding-style": ["indent", "format", "style", "naming", "convention", "lint"],
    "environment": ["venv", "env", "docker", "port", "database", "redis", "postgres"],
    "workflow": ["commit", "deploy", "test", "build", "ci", "cd", "pipeline"],
    "debugging": ["debug", "error", "log", "trace", "breakpoint"],
}

# 副作用検出キーワード
_SIDE_EFFECT_KEYWORDS_JA = ["副作用", "残留", "意図しない", "再帰的"]
_SIDE_EFFECT_KEYWORDS_EN = ["side effect", "unintended", "residual", "recursive", "leftover"]
_SIDE_EFFECT_COMPOUND_PATTERNS = [
    re.compile(r"pending.*(?:残留|table|テーブル)", re.IGNORECASE),
]

# モデル名キーワード
_MODEL_KEYWORDS = [
    "gpt-", "claude-", "gemini-", "o3", "o4", "model", "llm",
    "sonnet", "opus", "haiku",
]

# last_skill コンテキスト層の confidence 値
LAST_SKILL_CONFIDENCE = 0.88

# paths frontmatter 提案
PATHS_SUGGESTION_MIN_FILES = 1


@dataclass
class PathsSuggestion:
    """paths frontmatter の提案。"""
    patterns: List[str]
    confidence: float


def detect_project_signals(
    message: str,
    project_root: Optional[Path] = None,
) -> bool:
    """correction テキストにプロジェクト固有のシグナルが含まれるかを判定する。"""
    root = project_root or Path.cwd()
    msg_lower = message.lower()

    skill_triggers = extract_skill_triggers(project_root=root)
    for entry in skill_triggers:
        skill_name = entry["skill"]
        if skill_name.lower() in msg_lower or f"/{skill_name}".lower() in msg_lower:
            return True

    path_candidates = re.findall(r"(?:^|\s)([\w./\-]+/)", message)
    for candidate in path_candidates:
        candidate_path = root / candidate
        if candidate_path.is_dir():
            return True

    return False


def detect_side_effect_correction(message: str) -> bool:
    """correction メッセージに副作用見落としパターンが含まれるかを判定する。"""
    msg_lower = message.lower()
    for kw in _SIDE_EFFECT_KEYWORDS_JA:
        if kw in message:
            return True
    for kw in _SIDE_EFFECT_KEYWORDS_EN:
        if kw in msg_lower:
            return True
    for pattern in _SIDE_EFFECT_COMPOUND_PATTERNS:
        if pattern.search(message):
            return True
    return False


def _resolve_skill_references_path(
    skill_name: str,
    project_root: Path,
) -> Optional[Tuple[str, float]]:
    """last_skill のスキル references/ パスを解決する。"""
    skill_dir = project_root / ".claude" / "skills" / skill_name
    global_skill_dir = Path.home() / ".claude" / "skills" / skill_name

    for candidate_dir in [skill_dir, global_skill_dir]:
        if candidate_dir.is_dir() and is_protected_skill(candidate_dir):
            alt_path, _exists = suggest_local_alternative(skill_name, project_root)
            return (alt_path, LAST_SKILL_CONFIDENCE)

    refs_path = skill_dir / "references" / "pitfalls.md"
    return (str(refs_path), LAST_SKILL_CONFIDENCE)


def suggest_claude_file(
    correction: Dict[str, Any],
    project_root: Optional[Path] = None,
) -> Optional[Tuple[str, float]]:
    """correction のテキストからルーティング先を提案する。"""
    root = project_root or Path.cwd()
    message = correction.get("message", "")
    ctype = correction.get("correction_type", "")
    confidence = correction.get("confidence", 0.5)
    msg_lower = message.lower()

    # 1. guardrail タイプ → .claude/rules/guardrails.md
    if correction.get("guardrail") or ctype == "guardrail" or correction.get("sentiment") == "guardrail":
        return (str(root / ".claude" / "rules" / "guardrails.md"), 0.90)

    # 2. プロジェクト固有シグナル → .claude/rules/
    if detect_project_signals(message, project_root=root):
        return (str(root / ".claude" / "rules" / "project-specific.md"), 0.85)

    # 3. 副作用見落としパターン → .claude/rules/verification.md
    if detect_side_effect_correction(message):
        return (str(root / ".claude" / "rules" / "verification.md"), 0.85)

    # 4. モデル名キーワード → ~/.claude/CLAUDE.md or model-preferences rule
    if any(kw in msg_lower for kw in _MODEL_KEYWORDS):
        model_rule = root / ".claude" / "rules" / "model-preferences.md"
        if model_rule.exists():
            return (str(model_rule), 0.85)
        return (str(Path.home() / ".claude" / "CLAUDE.md"), 0.85)

    # 5. "always/never/prefer" → ~/.claude/CLAUDE.md (global behavior)
    if re.search(r"\b(always|never|prefer)\b", msg_lower):
        return (str(Path.home() / ".claude" / "CLAUDE.md"), 0.80)

    # 6. last_skill コンテキスト → スキルの references/ にルーティング
    last_skill = correction.get("last_skill")
    if last_skill:
        result = _resolve_skill_references_path(last_skill, root)
        if result is not None:
            return result

    # 7. rule の paths: frontmatter にマッチ
    rules_dir = root / ".claude" / "rules"
    if rules_dir.is_dir():
        for rule_file in sorted(rules_dir.glob("*.md")):
            fm = _parse_rule_frontmatter(rule_file)
            paths = fm.get("paths", [])
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list):
                continue
            for p in paths:
                if isinstance(p, str) and p in msg_lower:
                    return (str(rule_file), 0.80)

    # 8. サブディレクトリ名にマッチ
    for item in sorted(root.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            claude_file = item / "CLAUDE.md"
            if claude_file.exists() and item.name.lower() in msg_lower:
                return (str(claude_file), 0.75)

    # 9. 低信頼度 (confidence < 0.75) → auto-memory
    if confidence < 0.75:
        topic = suggest_auto_memory_topic(message)
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", str(root))
        encoded = project_dir.replace("/", "-")
        for candidate in [encoded, encoded.lstrip("-")]:
            memory_dir = Path.home() / ".claude" / "projects" / candidate / "memory"
            if memory_dir.is_dir():
                return (str(memory_dir / f"{topic}.md"), 0.60)
        memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"
        return (str(memory_dir / f"{topic}.md"), 0.60)

    return None


def suggest_auto_memory_topic(text: str) -> str:
    """テキストからトピックを判定する。キーワードスコアリングで最適トピックを選択。"""
    text_lower = text.lower()
    scores: Dict[str, int] = {}

    for topic, keywords in _AUTO_MEMORY_TOPICS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[topic] = score

    if not scores:
        return "general"

    return max(scores, key=scores.get)  # type: ignore[arg-type]


def suggest_paths_frontmatter(
    message: str, project_root: Path
) -> Optional[PathsSuggestion]:
    """correction テキストからファイルパスパターンを抽出し paths グロブを提案する。"""
    extracted = extract_paths_outside_codeblocks(message)
    if len(extracted) < PATHS_SUGGESTION_MIN_FILES:
        return None

    paths = [p for _, p in extracted]

    parsed = []
    for p in paths:
        pp = PurePosixPath(p)
        ext = pp.suffix
        parent = str(pp.parent) if str(pp.parent) != "." else ""
        parsed.append((parent, ext))

    if not parsed:
        return None

    ext_groups: Dict[str, List[str]] = {}
    for parent, ext in parsed:
        ext_groups.setdefault(ext, []).append(parent)

    patterns = []
    confidence = 0.0

    ext_with_ext = {k: v for k, v in ext_groups.items() if k}
    ext_without = ext_groups.get("", [])

    for ext, dirs in ext_with_ext.items():
        prefix = _common_path_prefix(dirs)
        if prefix:
            patterns.append(f"{prefix}/**/*{ext}")
            confidence = max(confidence, 0.85)
        else:
            patterns.append(f"**/*{ext}")
            confidence = max(confidence, 0.60)

    if ext_without and not ext_with_ext:
        return None

    if not patterns:
        return None

    return PathsSuggestion(patterns=patterns, confidence=confidence)


def _common_path_prefix(dirs: List[str]) -> str:
    """ディレクトリリストから共通プレフィックスを算出する。"""
    non_empty = [d for d in dirs if d]
    if not non_empty:
        return ""
    if len(non_empty) == 1:
        return non_empty[0]

    split_dirs = [d.split("/") for d in non_empty]
    common = []
    for parts in zip(*split_dirs):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break

    return "/".join(common)
