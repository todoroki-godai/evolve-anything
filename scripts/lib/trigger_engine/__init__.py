"""Auto-evolve trigger engine — セッション終了・corrections 蓄積時のトリガー評価。

トリガー条件の統合判定、クールダウン管理、ユーザー設定の読み込みを提供する。
LLM 呼び出しは行わない（MUST NOT）。

Phase 9 で `trigger_engine.py` 751 行 → `trigger_engine/` パッケージに分割。
公開 API (`from trigger_engine import X`) は本ファイルからの再エクスポートで維持される。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"
EVOLVE_STATE_FILE = DATA_DIR / "evolve-state.json"
PENDING_TRIGGER_FILE = DATA_DIR / "pending-trigger.json"
SNOOZE_FILE = DATA_DIR / "trigger-snooze.json"

try:
    import duckdb as _duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

# FileChanged hook cooldown (seconds) — CC v2.1.83
FILE_CHANGED_COOLDOWN_SECONDS = 300  # 5 minutes

# Re-export state / config / cooldown helpers (Phase 9 / Slice 1)
from .state import (  # noqa: E402, F401
    DEFAULT_TRIGGER_CONFIG,
    TriggerResult,
    _FIRST_RUN_MIN_SESSIONS,
    _MAX_HISTORY_ENTRIES,
    _count_sessions_since,
    _deep_merge,
    _is_in_cooldown,
    _load_state,
    _load_user_config_with_explicit,
    _record_trigger,
    _save_state,
    load_trigger_config,
)


# Re-export bloat / file_change helpers (Phase 9 / Slice 2)
from .bloat import _build_bloat_message, _evaluate_bloat  # noqa: E402, F401
from .file_change import evaluate_file_changed, is_watched_file  # noqa: E402, F401


# Re-export session-end + corrections evaluators (Phase 9 / Slice 3)
from .session_corrections import evaluate_corrections, evaluate_session_end  # noqa: E402, F401



# --- Evaluate: self-evolution ---


def _evaluate_self_evolution(state: dict[str, Any] | None = None) -> TriggerResult:
    """False positive 蓄積に基づく self-evolution トリガーを評価する。"""
    if state is None:
        state = _load_state()

    config = load_trigger_config(state)
    if not config.get("enabled", True):
        return TriggerResult(triggered=False)

    # Load self-evolution config
    se_config = state.get("trigger_config", {}).get("self_evolution", {})
    fp_threshold = se_config.get("false_positive_rate_threshold", 0.3)
    min_per_type = se_config.get("min_outcomes_per_type", 10)
    cooldown_hours = se_config.get("self_evolution_cooldown_hours", 72)
    lookback_days = se_config.get("analysis_lookback_days", 30)

    if _is_in_cooldown(state, "self_evolution", cooldown_hours):
        return TriggerResult(triggered=False, reason="cooldown")

    # Load outcomes
    outcomes_file = DATA_DIR / "remediation-outcomes.jsonl"
    if not outcomes_file.exists():
        return TriggerResult(triggered=False)

    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).isoformat()

    by_type: dict[str, dict[str, int]] = {}
    for line in outcomes_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("timestamp", "") < cutoff:
            continue
        it = rec.get("issue_type", "unknown")
        if it not in by_type:
            by_type[it] = {"total": 0, "rejected": 0, "skipped": 0}
        by_type[it]["total"] += 1
        decision = rec.get("user_decision", rec.get("result", ""))
        if decision == "rejected":
            by_type[it]["rejected"] += 1
        elif decision == "skipped":
            by_type[it]["skipped"] += 1

    # Check if any type exceeds threshold
    triggered_types: list[str] = []
    for it, stats in by_type.items():
        if stats["total"] < min_per_type:
            continue
        fp_rate = (stats["rejected"] + stats["skipped"]) / stats["total"]
        if fp_rate >= fp_threshold:
            triggered_types.append(it)

    if not triggered_types:
        return TriggerResult(triggered=False)

    result = TriggerResult(
        triggered=True,
        reason="self_evolution",
        action="/rl-anything:evolve",
        message=f"False positive 蓄積検出: {', '.join(triggered_types)}。self-evolution を推奨。",
        details={"triggered_types": triggered_types},
    )
    state = _record_trigger(state, result)
    _save_state(state)
    return result


def _evaluate_approval_rate_decline(state: dict[str, Any] | None = None) -> TriggerResult:
    """承認率の継続的低下に基づくトリガーを評価する。"""
    if state is None:
        state = _load_state()

    config = load_trigger_config(state)
    if not config.get("enabled", True):
        return TriggerResult(triggered=False)

    se_config = state.get("trigger_config", {}).get("self_evolution", {})
    decline_threshold = se_config.get("approval_rate_decline_threshold", 0.2)
    sample_size = se_config.get("decline_sample_size", 10)
    cooldown_hours = se_config.get("self_evolution_cooldown_hours", 72)

    if _is_in_cooldown(state, "approval_rate_decline", cooldown_hours):
        return TriggerResult(triggered=False, reason="cooldown")

    outcomes_file = DATA_DIR / "remediation-outcomes.jsonl"
    if not outcomes_file.exists():
        return TriggerResult(triggered=False)

    records: list[dict[str, Any]] = []
    for line in outcomes_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Need at least 2 * sample_size records
    if len(records) < 2 * sample_size:
        return TriggerResult(triggered=False)

    recent = records[-sample_size:]
    previous = records[-(2 * sample_size):-sample_size]

    def _approval_rate(recs: list[dict[str, Any]]) -> float:
        if not recs:
            return 0.0
        approved = sum(
            1 for r in recs
            if r.get("user_decision") == "approved" or r.get("result") == "success"
        )
        return approved / len(recs)

    recent_rate = _approval_rate(recent)
    previous_rate = _approval_rate(previous)
    decline = previous_rate - recent_rate

    if decline < decline_threshold:
        return TriggerResult(triggered=False)

    result = TriggerResult(
        triggered=True,
        reason="approval_rate_decline",
        action="/rl-anything:evolve",
        message=(
            f"承認率低下検出: {previous_rate:.0%} → {recent_rate:.0%} "
            f"(Δ{decline:.0%})。self-evolution を推奨。"
        ),
        details={
            "previous_rate": round(previous_rate, 4),
            "recent_rate": round(recent_rate, 4),
            "decline": round(decline, 4),
        },
    )
    state = _record_trigger(state, result)
    _save_state(state)
    return result


# --- Pending trigger file management ---


def write_pending_trigger(result: TriggerResult) -> None:
    """pending-trigger.json にトリガー結果を書き出す。"""
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snoozed_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {"snoozed_until": snoozed_until.isoformat()}
    SNOOZE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_snooze() -> None:
    """スヌーズを解除する。evolve 実行時に呼ぶ。"""
    try:
        SNOOZE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _is_snoozed() -> bool:
    """スヌーズ中かどうか判定する。期限切れならスヌーズファイルを削除。"""
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


# --- Skill change detection ---


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
