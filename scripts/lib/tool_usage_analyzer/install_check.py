"""artifact / hook 導入状態の確認モジュール (Phase 6 / Slice 3)。

`check_artifact_installed` (汎用 artifact 導入確認) と
`check_hook_installed` (check-bash-builtin hook 導入確認) を提供する。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def check_artifact_installed(
    artifact: Dict[str, Any],
    *,
    hooks_dir: Optional[Path] = None,
    rules_dir: Optional[Path] = None,
    settings_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """推奨 artifact の導入状態を確認する。

    Returns:
        {"installed": bool, "artifacts_found": list[str],
         "content_matched": bool | None}
    """
    artifacts_found: List[str] = []
    content_matched: Optional[bool] = None

    # hook_path チェック
    hook_path = artifact.get("hook_path")
    if hook_path:
        try:
            if hook_path.exists():
                artifacts_found.append("hook")
        except OSError:
            pass

    # rule path チェック
    rule_path = artifact.get("path")
    if rule_path:
        try:
            if rule_path.exists():
                artifacts_found.append("rule")
        except OSError:
            pass

    # content_patterns チェック
    content_patterns = artifact.get("content_patterns")
    if content_patterns and hook_path:
        try:
            if hook_path.exists():
                import re
                hook_content = hook_path.read_text(encoding="utf-8")
                all_matched = all(
                    re.search(pattern, hook_content)
                    for pattern in content_patterns
                )
                content_matched = all_matched
            else:
                content_matched = False
        except OSError:
            content_matched = None

    # installed 判定: 必要な artifact が全て存在 + content_pattern マッチ
    rule_ok = rule_path is None or "rule" in artifacts_found
    hook_ok = hook_path is None or "hook" in artifacts_found
    content_ok = content_matched is not False if content_patterns else True
    installed = rule_ok and hook_ok and content_ok

    return {
        "installed": installed,
        "artifacts_found": artifacts_found,
        "content_matched": content_matched,
    }


def check_hook_installed(
    *,
    hook_path: Optional[Path] = None,
    settings_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """check-bash-builtin hook の導入状態を確認する。

    Returns:
        {"installed": bool, "hook_exists": bool, "settings_registered": bool}
    """
    if hook_path is None:
        from . import GLOBAL_HOOKS_DIR
        hook_path = GLOBAL_HOOKS_DIR / "check-bash-builtin.py"
    if settings_path is None:
        settings_path = Path.home() / ".claude" / "settings.json"

    hook_exists = hook_path.exists()

    settings_registered = False
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            for hook_group in settings.get("hooks", {}).get("PreToolUse", []):
                for hook in hook_group.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "check-bash-builtin" in cmd:
                        settings_registered = True
                        break
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "installed": hook_exists and settings_registered,
        "hook_exists": hook_exists,
        "settings_registered": settings_registered,
    }
