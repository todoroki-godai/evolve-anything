"""8層メモリ階層のファイル探索・読み書きユーティリティ。"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def find_claude_files(project_root: Optional[Path] = None) -> Dict[str, List[Path]]:
    """8層メモリ階層のファイルパスを探索して返す。

    Args:
        project_root: プロジェクトルート。None の場合は cwd を使用。

    Returns:
        層名 → ファイルパスリストの辞書。
    """
    root = project_root or Path.cwd()
    home = Path.home()
    result: Dict[str, List[Path]] = {
        "global": [],
        "root": [],
        "local": [],
        "subdirectory": [],
        "rule": [],
        "user-rule": [],
        "auto-memory": [],
        "skill": [],
    }

    # global: ~/.claude/CLAUDE.md
    global_file = home / ".claude" / "CLAUDE.md"
    if global_file.exists():
        result["global"].append(global_file)

    # root: ./CLAUDE.md
    root_file = root / "CLAUDE.md"
    if root_file.exists():
        result["root"].append(root_file)

    # local: ./CLAUDE.local.md
    local_file = root / "CLAUDE.local.md"
    if local_file.exists():
        result["local"].append(local_file)

    # subdirectory: ./**/CLAUDE.md (root 直下は除外)
    for p in root.rglob("CLAUDE.md"):
        if p == root_file:
            continue
        # .claude ディレクトリ内は除外
        try:
            p.relative_to(root / ".claude")
            continue
        except ValueError:
            pass
        result["subdirectory"].append(p)

    # rule: ./.claude/rules/*.md
    rules_dir = root / ".claude" / "rules"
    if rules_dir.is_dir():
        result["rule"] = sorted(rules_dir.glob("*.md"))

    # user-rule: ~/.claude/rules/*.md
    user_rules_dir = home / ".claude" / "rules"
    if user_rules_dir.is_dir():
        result["user-rule"] = sorted(user_rules_dir.glob("*.md"))

    # auto-memory: ~/.claude/projects/<project>/memory/*.md
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", str(root))
    # Claude Code の auto-memory パスは project_dir を "-" 区切りにエンコード
    encoded = project_dir.replace("/", "-")
    # 先頭ハイフンあり/なし両方を試す（Claude Code バージョンにより異なる）
    for candidate in [encoded, encoded.lstrip("-")]:
        memory_dir = home / ".claude" / "projects" / candidate / "memory"
        if memory_dir.is_dir():
            result["auto-memory"] = sorted(memory_dir.glob("*.md"))
            break

    # skill: ./.claude/commands/*/SKILL.md
    commands_dir = root / ".claude" / "commands"
    if commands_dir.is_dir():
        for skill_dir in sorted(commands_dir.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                result["skill"].append(skill_file)

    return result


def read_auto_memory(project_path: Optional[str] = None) -> List[Dict[str, str]]:
    """auto-memory ディレクトリのファイルを読み取る。

    Args:
        project_path: プロジェクトパス。None の場合は CLAUDE_PROJECT_DIR or cwd。

    Returns:
        各ファイルの {path, topic, content} のリスト。
    """
    proj = project_path or os.environ.get("CLAUDE_PROJECT_DIR", str(Path.cwd()))
    encoded = proj.replace("/", "-")
    memory_dir = None
    for candidate in [encoded, encoded.lstrip("-")]:
        candidate_dir = Path.home() / ".claude" / "projects" / candidate / "memory"
        if candidate_dir.is_dir():
            memory_dir = candidate_dir
            break
    if memory_dir is None:
        memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"

    entries = []
    if not memory_dir.is_dir():
        return entries

    for md_file in sorted(memory_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        entries.append({
            "path": str(md_file),
            "topic": md_file.stem,
            "content": content,
        })
    return entries


def read_all_memory_entries(project_root: Optional[Path] = None) -> List[Dict[str, str]]:
    """全メモリ層のエントリを読み取る。

    Args:
        project_root: プロジェクトルート。

    Returns:
        各ファイルの {tier, path, content} のリスト。
    """
    files = find_claude_files(project_root)
    entries = []
    for tier, paths in files.items():
        for p in paths:
            try:
                content = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            entries.append({
                "tier": tier,
                "path": str(p),
                "content": content,
            })
    return entries


def split_memory_sections(
    content: str,
    file_path: str = "",
) -> List[Dict[str, Any]]:
    """MEMORY コンテンツを ``## `` 見出し単位でセクション分割する。

    Args:
        content: MEMORY ファイルの全文。
        file_path: ファイルパス（出力に含める）。

    Returns:
        各セクションの ``{file, heading, content, line_range}`` のリスト。
        見出しの無い先頭部分は ``heading: "_header"`` として返す。
    """
    lines = content.splitlines()
    sections: List[Dict[str, Any]] = []
    current_heading = "_header"
    current_lines: List[str] = []
    start_line = 1

    for i, line in enumerate(lines, start=1):
        if line.startswith("## ") and not line.startswith("### "):
            body = "\n".join(current_lines).strip()
            if body:
                sections.append({
                    "file": file_path,
                    "heading": current_heading,
                    "content": body,
                    "line_range": [start_line, i - 1],
                })
            current_heading = line[3:].strip()
            current_lines = []
            start_line = i
        else:
            current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if body:
        sections.append({
            "file": file_path,
            "heading": current_heading,
            "content": body,
            "line_range": [start_line, len(lines)],
        })

    return sections
