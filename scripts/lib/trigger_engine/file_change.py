"""FileChanged hook (CC v2.1.83) のトリガー評価。

`is_watched_file` でカテゴリ分類し、`evaluate_file_changed` で audit 提案を生成する。
クールダウンは `FILE_CHANGED_COOLDOWN_SECONDS` (5分) をカテゴリ別に適用。

注: モジュール定数 (`FILE_CHANGED_COOLDOWN_SECONDS`) は package 経由で
`from . import FILE_CHANGED_COOLDOWN_SECONDS` の遅延参照で取得する
（テストの `mock.patch("trigger_engine.X", ...)` 追従のため）。
"""
from __future__ import annotations

from typing import Any

from .state import (
    TriggerResult,
    _is_in_cooldown,
    _load_state,
    _load_user_config_with_explicit,
    _record_trigger,
    _save_state,
)


def is_watched_file(file_path: str) -> str | None:
    """ファイルパスを rl-anything 関連カテゴリに分類する。

    Returns:
        "claude_md" / "skills" / "rules" / None
    """
    if file_path.endswith("/CLAUDE.md") or file_path == "CLAUDE.md":
        return "claude_md"
    if "/SKILL.md" in file_path and ".claude/skills/" in file_path:
        return "skills"
    if ".claude/rules/" in file_path and file_path.endswith(".md"):
        return "rules"
    return None


def evaluate_file_changed(
    file_path: str,
    *,
    state: dict[str, Any] | None = None,
    project_dir: str | None = None,
) -> TriggerResult:
    """FileChanged イベントを評価し、audit 提案を生成する。

    クールダウンは FILE_CHANGED_COOLDOWN_SECONDS (5分) をカテゴリ別に適用。
    userConfig の auto_trigger=false で無効化可能。
    """
    from . import FILE_CHANGED_COOLDOWN_SECONDS

    category = is_watched_file(file_path)
    if category is None:
        return TriggerResult(triggered=False)

    # userConfig gate
    try:
        user_config, _ = _load_user_config_with_explicit()
    except Exception:
        user_config = {"auto_trigger": True}

    if not user_config.get("auto_trigger", True):
        return TriggerResult(triggered=False)

    if state is None:
        state = _load_state()

    reason = f"file_changed:{category}"
    cooldown_hours = FILE_CHANGED_COOLDOWN_SECONDS / 3600

    if _is_in_cooldown(state, reason, cooldown_hours):
        return TriggerResult(triggered=False)

    result = TriggerResult(
        triggered=True,
        reason=reason,
        action="/rl-anything:audit",
        message=f"{category} ファイルが変更されました。推奨: /rl-anything:audit",
        details={"file_path": file_path, "category": category},
    )
    state = _record_trigger(state, result)
    _save_state(state)
    return result
