"""Pending trigger ファイル管理 + スヌーズ + skill 変更検出。

`pending-trigger.json` の write/read-and-delete、`trigger-snooze.json` のスヌーズ管理、
git diff ベースの SKILL.md 変更検出を提供する。

注: `DATA_DIR` / `PENDING_TRIGGER_FILE` / `SNOOZE_FILE` は package 経由で
`from . import X` 関数内 lazy lookup（テストの `mock.patch("trigger_engine.X")` 追従）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .state import TriggerResult


def write_pending_trigger(result: TriggerResult) -> None:
    """pending-trigger.json にトリガー結果を書き出す。"""
    from . import DATA_DIR, PENDING_TRIGGER_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "triggered": result.triggered,
        "reason": result.reason,
        "action": result.action,
        "message": result.message,
        "details": result.details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    PENDING_TRIGGER_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_and_delete_pending_trigger() -> dict[str, Any] | None:
    """pending-trigger.json を読み取り、削除する。スヌーズ中は配信しない。"""
    from . import PENDING_TRIGGER_FILE
    if not PENDING_TRIGGER_FILE.exists():
        return None
    if _is_snoozed():
        return None
    try:
        data = json.loads(PENDING_TRIGGER_FILE.read_text(encoding="utf-8"))
        PENDING_TRIGGER_FILE.unlink()
        return data
    except (json.JSONDecodeError, OSError):
        try:
            PENDING_TRIGGER_FILE.unlink()
        except OSError:
            pass
        return None


def snooze_trigger(hours: float = 24) -> None:
    """トリガー通知をスヌーズする。hours 後に再通知。"""
    from . import DATA_DIR, SNOOZE_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snoozed_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {"snoozed_until": snoozed_until.isoformat()}
    SNOOZE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_snooze() -> None:
    """スヌーズを解除する。evolve 実行時に呼ぶ。"""
    from . import SNOOZE_FILE
    try:
        SNOOZE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _is_snoozed() -> bool:
    """スヌーズ中かどうか判定する。期限切れならスヌーズファイルを削除。"""
    from . import SNOOZE_FILE
    if not SNOOZE_FILE.exists():
        return False
    try:
        data = json.loads(SNOOZE_FILE.read_text(encoding="utf-8"))
        snoozed_until = datetime.fromisoformat(data["snoozed_until"])
        if datetime.now(timezone.utc) < snoozed_until:
            return True
        # 期限切れ → クリーンアップ
        SNOOZE_FILE.unlink(missing_ok=True)
        return False
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        # 壊れたファイル → 削除
        try:
            SNOOZE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def detect_skill_changes() -> list[str]:
    """git diff で .claude/skills/*/SKILL.md の変更を検出する。"""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", ".claude/skills/*/SKILL.md"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        changed = []
        for line in result.stdout.strip().splitlines():
            # Extract skill name from path like .claude/skills/my-skill/SKILL.md
            parts = line.split("/")
            if len(parts) >= 4:
                changed.append(parts[2])
        return changed
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
