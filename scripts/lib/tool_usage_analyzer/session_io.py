"""セッション JSONL からツール呼び出しを抽出するモジュール。

`_resolve_session_dir` / `extract_tool_calls` / `extract_tool_calls_by_session`
を提供する (Phase 6 / Slice 1)。
"""
import json
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _resolve_session_dir(
    project_root: Optional[Path] = None,
    projects_dir: Optional[Path] = None,
) -> Optional[Path]:
    """project_root からセッション JSONL のディレクトリを解決する。"""
    if projects_dir is None:
        # 既定は call-time の HOME から再解決する。module-level の CLAUDE_PROJECTS_DIR は
        # import 時に Path.home() を凍結するため、テストの HOME 隔離（#457）を擦り抜け、
        # xdist で先に import した worker が実 ~/.claude/projects を読む非hermetic 露出
        # （keyset snapshot drift）の根因になっていた。CLAUDE_PROJECTS_DIR が import 時の
        # 凍結値から差し替えられている場合（mock.patch 等）はそれを尊重する。
        from . import CLAUDE_PROJECTS_DIR, _IMPORT_TIME_PROJECTS_DIR
        if CLAUDE_PROJECTS_DIR != _IMPORT_TIME_PROJECTS_DIR:
            projects_dir = CLAUDE_PROJECTS_DIR
        else:
            projects_dir = Path.home() / ".claude" / "projects"

    if not projects_dir.is_dir():
        return None

    if project_root is None:
        return None

    # プロジェクト名から slug を探索（discover.py と同パターン）
    project_name = project_root.name
    for d in projects_dir.iterdir():
        if d.is_dir() and d.name.endswith(project_name):
            return d

    return None


def extract_tool_calls(
    project_root: Optional[Path] = None,
    *,
    projects_dir: Optional[Path] = None,
) -> Tuple[Counter, List[str]]:
    """セッション JSONL からツール呼び出しを抽出する。

    Returns:
        (tool_counts, bash_commands): ツール名別カウントと Bash コマンド文字列リスト
    """
    session_dir = _resolve_session_dir(project_root, projects_dir)
    if session_dir is None:
        return Counter(), []

    tool_counts: Counter = Counter()
    bash_commands: List[str] = []

    for session_file in session_dir.glob("*.jsonl"):
        try:
            text = session_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get("type") != "assistant":
                continue

            msg = rec.get("message", {})
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_use":
                    continue

                name = item.get("name", "")
                tool_counts[name] += 1

                if name == "Bash":
                    cmd = item.get("input", {}).get("command", "")
                    if cmd:
                        bash_commands.append(cmd)

    return tool_counts, bash_commands


def extract_tool_calls_by_session(
    project_root: Optional[Path] = None,
    *,
    projects_dir: Optional[Path] = None,
    max_age_days: Optional[int] = None,
) -> Dict[str, List[str]]:
    """セッション JSONL からセッション単位で Bash コマンドを抽出する。

    Returns:
        {session_id: [command_strings]} — セッション ID はファイル名 stem。
    """
    session_dir = _resolve_session_dir(project_root, projects_dir)
    if session_dir is None:
        return {}

    result: Dict[str, List[str]] = {}
    now = time.time()

    for session_file in session_dir.glob("*.jsonl"):
        # recency フィルタ
        if max_age_days is not None:
            try:
                mtime = session_file.stat().st_mtime
                if (now - mtime) > max_age_days * 86400:
                    continue
            except OSError:
                continue

        commands: List[str] = []
        try:
            text = session_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get("type") != "assistant":
                continue

            msg = rec.get("message", {})
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_use":
                    continue
                if item.get("name") != "Bash":
                    continue
                cmd = item.get("input", {}).get("command", "")
                if cmd:
                    commands.append(cmd)

        if commands:
            result[session_file.stem] = commands

    return result
