"""agency-agents upstream 監視ユーティリティ。"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from agent_quality_catalog import UPSTREAM_REPO

logger = logging.getLogger(__name__)


def check_upstream(
    *,
    state_file: Optional[Path] = None,
    repo: str = UPSTREAM_REPO,
) -> Dict[str, Any]:
    """agency-agents リポジトリの更新をチェックする。

    前回チェック時のコミットハッシュと比較し、更新有無を返す。
    gh api 失敗時は graceful に skip する。
    """
    current_hash = _fetch_latest_commit_hash(repo)
    if current_hash is None:
        return {
            "status": "error",
            "message": f"Failed to fetch latest commit from {repo}",
        }

    stored_hash = None
    if state_file is not None and state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            stored_hash = state.get("upstream_commit_hash")
        except (json.JSONDecodeError, OSError):
            pass

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
    """gh api でリポジトリの最新コミットハッシュを取得する。失敗時は None。"""
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
