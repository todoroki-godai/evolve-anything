#!/usr/bin/env python3
"""#475 §6.2: 反映先ファイルへの起草行の適用を確認する正規化・一致判定の単一ソース。

`reflect.py` の `update_reflect_status(status="applied")` が「反映先ファイルに該当行が
実在するか」を決定論（LLM 不要）で確認するために使う。判定は起草行の**正規化後の完全
一致**のみ（部分一致・LLM 判定はしない）。

正規化規則（実測: `~/.claude/rules/*.md` 33件 + `<repo>/.claude/rules/*.md` 14件 = 47
ファイル・本文282行。`- ` 始まり208行=74% / 見出し47行=17% / 素の文27行=10% /
番号付き・チェックボックス・引用は0件）:

1. 対象ファイルに `- ` 始まりの行が1行でもあれば「箇条書きファイル」と判定し、
   前後空白 + 行頭 `- ` を除去して完全一致させる。
2. `- ` 始まりの行が0行なら「素の文ファイル」と判定し、前後空白の除去のみで
   行全体の完全一致させる。
3. 番号付き・チェックボックス・引用等の未知の行頭記号に draft_line 自身が当たったら、
   「一致なし」で確定させず `unknown_line_prefix` を返す（黙って失敗にしない）。
"""
import re
from pathlib import Path
from typing import Dict, List, Optional

_BULLET_PREFIX = "- "

# 実データに0件だが将来出現しうる行頭記号（#475 §6.2 rule 3）。
# 番号付き（"1. "）/ チェックボックス（"- [ ] " / "- [x] "）/ 引用（"> "）/ 表（"| ...|"）。
_UNKNOWN_PREFIX_RE = re.compile(r"^(\d+\.\s|-\s\[[ xX]\]\s|>\s|\|)")

_KNOWN_TARGET_KINDS = frozenset({
    "global_rule",
    "project_rule",
    "global_claude_md",
    "project_claude_md",
    "skill",
    "other",
})


def classify_file(lines: List[str]) -> str:
    """`- ` 始まりの行（インデント許容）が1行でもあれば "bullet"、無ければ "plain"。"""
    for line in lines:
        if line.strip().startswith(_BULLET_PREFIX):
            return "bullet"
    return "plain"


def _normalize_bullet(line: str) -> str:
    s = line.strip()
    if s.startswith(_BULLET_PREFIX):
        s = s[len(_BULLET_PREFIX):].strip()
    return s


def _normalize_plain(line: str) -> str:
    return line.strip()


def check_line_applied(target_path: Path, draft_line: str) -> Dict[str, Optional[str]]:
    """draft_line が target_path に既に反映されているかを正規化後完全一致で判定する。

    Args:
        target_path: 反映先ファイル。
        draft_line: 起草行の全文（表示用の省略文ではなく照合用の全文を渡すこと）。

    Returns:
        {"matched": bool, "reason": str | None}。reason は matched=False のときのみ
        設定する（"file_not_found" / "unknown_line_prefix" / "no_match"）。
    """
    if _UNKNOWN_PREFIX_RE.match(draft_line.strip()):
        return {"matched": False, "reason": "unknown_line_prefix"}

    target_path = Path(target_path)
    if not target_path.exists():
        return {"matched": False, "reason": "file_not_found"}

    lines = target_path.read_text(encoding="utf-8").splitlines()
    kind = classify_file(lines)
    normalize = _normalize_bullet if kind == "bullet" else _normalize_plain
    target_norm = normalize(draft_line)

    for line in lines:
        if normalize(line) == target_norm:
            return {"matched": True, "reason": None}

    return {"matched": False, "reason": "no_match"}


def classify_reflect_target_kind(target_path: str) -> str:
    """反映先ファイルの種別を分類する（#587 blocking (b)）。"""
    from evolve_decision_ids import global_skills_root, repo_identity
    from evolve_revert._target import global_rules_root

    path = Path(target_path).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    try:
        resolved.relative_to(global_rules_root().resolve())
        return "global_rule"
    except ValueError:
        pass

    if resolved == (Path.home() / ".claude" / "CLAUDE.md").resolve():
        return "global_claude_md"

    try:
        resolved.relative_to(global_skills_root().resolve())
        if resolved.name == "SKILL.md":
            return "skill"
    except ValueError:
        pass

    identity = repo_identity(str(path))
    repo_id = identity.get("repo_id")
    relative_path = (identity.get("relative_path") or "").replace("\\", "/")
    if repo_id:
        if relative_path.startswith(".claude/rules/"):
            return "project_rule"
        if relative_path == "CLAUDE.md":
            return "project_claude_md"
        if relative_path.endswith("/SKILL.md") or relative_path == "SKILL.md":
            if relative_path.startswith(".claude/skills/") or relative_path.startswith("skills/"):
                return "skill"

    return "other"


def normalize_reflect_target_path(target_path: str) -> str:
    """反映先を worktree 間で安定する監査・重複排除キーへ正規化する。"""
    from evolve_decision_ids import repo_identity

    path = Path(target_path).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    identity = repo_identity(str(resolved))
    repo_id = identity.get("repo_id")
    relative_path = identity.get("relative_path")
    if repo_id and relative_path:
        return f"{repo_id}:{str(relative_path).replace(chr(92), '/')}"
    return str(resolved)
