"""trajectory_sampler — raw sessions から TrajectoryRecord を抽出する。

~/.claude/projects/ 配下の *.jsonl を walk し、<command-name> タグを持つターンを
検出して、直前の user_prompt と直後の outcome を含む TrajectoryRecord を返す。

LLM 呼び出し一切なし。
--max-files N サンプリング対応（デフォルト: 50）。

Issue #238 Phase 1
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ── 定数 ──────────────────────────────────────────────────

DEFAULT_MAX_FILES = 50

# <command-name> タグから skill 名を取り出す正規表現
_COMMAND_NAME_RE = re.compile(r"<command-name>([^<]+)</command-name>")

# ビルトインコマンド（スキルではない）
_BUILTIN_COMMANDS = frozenset([
    "compact",
    "rename",
    "reload-plugins",
    "plugin",
    "clear",
    "help",
    "resume",
    "init",
    "config",
    "memory",
    "logout",
    "login",
    "status",
    "vim",
    "doctor",
])


# ── データクラス ──────────────────────────────────────────


@dataclass
class TrajectoryRecord:
    """スキル呼び出し1件のトラジェクトリ記録。"""

    skill_name: str
    """呼び出されたスキル名（先頭スラッシュ除去済み）。"""

    user_prompt: str
    """スキル呼び出し直前の user メッセージテキスト。"""

    outcome: str
    """success / failure / unknown のいずれか。"""

    session_id: str
    """セッション ID。"""

    timestamp: str
    """スキル呼び出しターンのタイムスタンプ（ISO 8601）。"""

    extra: dict = field(default_factory=dict)
    """追加情報（project パス等）。"""


# ── 公開関数 ──────────────────────────────────────────────


def sample_trajectories(
    projects_root: Optional[Path] = None,
    max_files: int = DEFAULT_MAX_FILES,
) -> List[TrajectoryRecord]:
    """~/.claude/projects/ 配下の *.jsonl を walk して TrajectoryRecord リストを返す。

    Args:
        projects_root: walk するルートディレクトリ。None の場合は
            ~/.claude/projects/ を使用する。
        max_files: サンプリングする最大ファイル数（transcript-store-bench 準拠）。

    Returns:
        TrajectoryRecord のリスト（スキル呼び出しが見つかったターン分）。
        ファイルが存在しない場合は空リストを返す。
    """
    if projects_root is None:
        projects_root = Path.home() / ".claude" / "projects"

    if not projects_root.exists():
        return []

    # *.jsonl ファイルを収集（max_files 件で early-exit してメモリを抑える）
    all_files: List[Path] = []
    for p in projects_root.rglob("*.jsonl"):
        all_files.append(p)
        if len(all_files) >= max_files:
            break

    if not all_files:
        return []

    records: List[TrajectoryRecord] = []
    for jsonl_path in all_files:
        try:
            records.extend(_parse_jsonl_file(jsonl_path))
        except (OSError, PermissionError):
            continue

    return records


def _parse_jsonl_file(jsonl_path: Path) -> List[TrajectoryRecord]:
    """単一の *.jsonl ファイルから TrajectoryRecord を抽出する。

    Args:
        jsonl_path: 対象の jsonl ファイルパス。

    Returns:
        抽出された TrajectoryRecord のリスト。
    """
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, PermissionError):
        return []

    turns = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not turns:
        return []

    records: List[TrajectoryRecord] = []

    for i, turn in enumerate(turns):
        skill_name = _extract_skill_from_turn(turn)
        if skill_name is None:
            continue

        # 直前の user_prompt を探す
        user_prompt = _find_preceding_user_prompt(turns, i)

        # 直後の assistant ターンから outcome を判定
        outcome = _determine_outcome(turns, i)

        # session_id と timestamp を取得
        session_id = _get_field(turn, "sessionId") or _get_field(turn, "session_id") or ""
        timestamp = _get_field(turn, "timestamp") or ""

        records.append(
            TrajectoryRecord(
                skill_name=skill_name,
                user_prompt=user_prompt,
                outcome=outcome,
                session_id=session_id,
                timestamp=timestamp,
                extra={"source_file": str(jsonl_path)},
            )
        )

    return records


def _extract_skill_from_turn(turn: dict) -> Optional[str]:
    """ターンから skill 名を抽出する。

    user ターンまたは system/local_command ターンの content から
    <command-name> タグを探し、スキル名（先頭スラッシュ除去）を返す。
    ビルトインコマンドは None を返す。

    Args:
        turn: セッションの1ターン（jsonl 1行）。

    Returns:
        スキル名文字列、またはスキルでない場合は None。
    """
    turn_type = turn.get("type", "")

    # assistant ターンは除外
    if turn_type == "assistant":
        return None

    # content を取得（user ターンは message.content、system は content フィールド）
    content = _get_content(turn)
    if not content:
        return None

    m = _COMMAND_NAME_RE.search(content)
    if not m:
        return None

    raw_name = m.group(1).strip()
    # 先頭スラッシュを除去
    skill_name = raw_name.lstrip("/")

    if not skill_name:
        return None

    # ビルトインコマンドを除外
    base_name = skill_name.split(":")[-1] if ":" in skill_name else skill_name
    if base_name.lower() in _BUILTIN_COMMANDS:
        return None
    # スラッシュのみのコマンドも除外
    if skill_name.lower() in _BUILTIN_COMMANDS:
        return None

    return skill_name


def _find_preceding_user_prompt(turns: list, command_index: int) -> str:
    """command_index より前の直近 user ターンのプロンプトテキストを返す。

    <command-name> を含まない純粋な user メッセージを探す。
    見つからない場合は空文字列を返す。
    """
    for j in range(command_index - 1, -1, -1):
        t = turns[j]
        if t.get("type") != "user":
            continue
        content = _get_content(t)
        if not content:
            continue
        # command-name タグを含まない通常メッセージのみ
        if "<command-name>" in content:
            continue
        # local-command-caveat 等のシステム挿入メッセージを除外
        if "<local-command" in content or "<command-message>" in content:
            continue
        return content.strip()
    return ""


def _determine_outcome(turns: list, command_index: int) -> str:
    """command_index の直後に assistant ターンがあれば success、なければ unknown を返す。

    将来的には tool_result / error 等で failure を判定することも可能。
    """
    for j in range(command_index + 1, min(command_index + 6, len(turns))):
        t = turns[j]
        if t.get("type") == "assistant":
            content = _get_content(t)
            if content:
                return "success"
    return "unknown"


def _get_content(turn: dict) -> str:
    """ターンの content 文字列を取得する。

    user/assistant ターンは message.content、
    system ターンは content フィールドを参照する。
    """
    msg = turn.get("message")
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # content がリスト形式の場合（tool_use blocks 等）
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            return " ".join(texts)
        return ""

    # system ターンは直接 content フィールド
    content = turn.get("content", "")
    if isinstance(content, str):
        return content
    return ""


def _get_field(turn: dict, field_name: str) -> Optional[str]:
    """ターンからトップレベルフィールドを取得する。"""
    val = turn.get(field_name)
    if isinstance(val, str):
        return val
    return None
