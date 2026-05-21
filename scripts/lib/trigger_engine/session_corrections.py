"""Session-end + corrections トリガー評価。

`evaluate_session_end` (audit_overdue / session_count / days_elapsed / bloat の
4 種類を OR 評価) と `evaluate_corrections` (corrections.jsonl 蓄積閾値) を提供する。

注: テストの `mock.patch("trigger_engine._evaluate_bloat" / ".DATA_DIR" / ...)`
追従のため、`_evaluate_bloat` / `_build_bloat_message` / `DATA_DIR` は
`from . import X` の関数内 lazy lookup で取得する。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .state import (
    TriggerResult,
    _FIRST_RUN_MIN_SESSIONS,
    _count_sessions_since,
    _is_in_cooldown,
    _load_state,
    _record_trigger,
    _save_state,
    load_trigger_config,
)


def evaluate_session_end(state: dict[str, Any] | None = None, *, project_dir: str | None = None) -> TriggerResult:
    """セッション終了時のトリガー条件を評価する。

    条件 (OR):
    1. 前回 evolve からのセッション数 >= min_sessions
    2. 前回 evolve からの経過日数 >= max_days
    3. 前回 audit からの経過日数 >= interval_days (audit_overdue)
    """
    # mock.patch("trigger_engine._evaluate_bloat" / "._build_bloat_message") 追従のため lazy lookup
    from . import _build_bloat_message, _evaluate_bloat

    if state is None:
        state = _load_state()

    config = load_trigger_config(state)
    if not config.get("enabled", True):
        return TriggerResult(triggered=False)

    cooldown_hours = config.get("cooldown_hours", 24)
    triggers_cfg = config.get("triggers", {})
    last_run = state.get("last_run_timestamp", "")
    now = datetime.now(timezone.utc)
    reasons: list[str] = []
    actions: list[str] = []
    details: dict[str, Any] = {}

    # --- audit_overdue (check first, independent action) ---
    audit_cfg = triggers_cfg.get("audit_overdue", {})
    audit_triggered = False
    if audit_cfg.get("enabled", True):
        interval_days = audit_cfg.get("interval_days", 30)
        last_audit = state.get("last_audit_timestamp", "")
        if not last_audit:
            # No previous audit → overdue
            audit_triggered = True
            details["audit_overdue"] = True
        else:
            try:
                last_audit_dt = datetime.fromisoformat(last_audit)
                elapsed = (now - last_audit_dt).total_seconds() / 86400
                if elapsed >= interval_days:
                    audit_triggered = True
                    details["audit_days_elapsed"] = round(elapsed, 1)
            except ValueError:
                audit_triggered = True

        if audit_triggered and not _is_in_cooldown(state, "audit_overdue", cooldown_hours):
            reasons.append("audit_overdue")
            actions.append("/rl-anything:audit")

    # --- session_end conditions ---
    se_cfg = triggers_cfg.get("session_end", {})
    if se_cfg.get("enabled", True):
        min_sessions = se_cfg.get("min_sessions", 10)
        max_days = se_cfg.get("max_days", 7)

        # Handle first run (no evolve-state.json)
        if not last_run:
            min_sessions = _FIRST_RUN_MIN_SESSIONS

        session_count = _count_sessions_since(last_run)
        details["session_count"] = session_count

        session_triggered = session_count >= min_sessions
        days_triggered = False

        if last_run:
            try:
                last_run_dt = datetime.fromisoformat(last_run)
                elapsed = (now - last_run_dt).total_seconds() / 86400
                details["days_since_evolve"] = round(elapsed, 1)
                if elapsed >= max_days:
                    days_triggered = True
            except ValueError:
                pass

        if session_triggered and not _is_in_cooldown(state, "session_count", cooldown_hours):
            reasons.append("session_count")
            actions.append("/rl-anything:evolve")
        if days_triggered and not _is_in_cooldown(state, "days_elapsed", cooldown_hours):
            reasons.append("days_elapsed")
            actions.append("/rl-anything:evolve")

    # --- bloat ---
    if project_dir and not _is_in_cooldown(state, "bloat", cooldown_hours):
        bloat_result = _evaluate_bloat(project_dir, config)
        if bloat_result:
            reasons.append("bloat")
            actions.append("/rl-anything:evolve")
            details["bloat_warnings"] = bloat_result["warnings"]

    if not reasons:
        return TriggerResult(triggered=False)

    # Build message
    primary_action = actions[0]
    unique_actions = list(dict.fromkeys(actions))
    msg_parts = []
    if "session_count" in reasons:
        msg_parts.append(f"前回 evolve から {details.get('session_count', '?')} セッション経過")
    if "days_elapsed" in reasons:
        msg_parts.append(f"前回 evolve から {details.get('days_since_evolve', '?')} 日経過")
    if "audit_overdue" in reasons:
        msg_parts.append("前回 audit から規定日数を超過")
    if "bloat" in reasons and "bloat_warnings" in details:
        msg_parts.append(f"肥大化検出: {_build_bloat_message({'warnings': details['bloat_warnings']})}")
    message = "。".join(msg_parts) + f"。推奨: {', '.join(unique_actions)}"

    result = TriggerResult(
        triggered=True,
        reason=reasons[0],  # primary reason
        action=primary_action,
        message=message,
        details={**details, "all_reasons": reasons, "all_actions": unique_actions},
    )

    # Record and save
    state = _record_trigger(state, result)
    _save_state(state)

    return result


def evaluate_corrections(state: dict[str, Any] | None = None) -> TriggerResult:
    """Corrections 蓄積閾値のトリガーを評価する。"""
    # mock.patch("trigger_engine.DATA_DIR") 追従のため lazy lookup
    from . import DATA_DIR

    if state is None:
        state = _load_state()

    config = load_trigger_config(state)
    if not config.get("enabled", True):
        return TriggerResult(triggered=False)

    triggers_cfg = config.get("triggers", {})
    corr_cfg = triggers_cfg.get("corrections", {})
    if not corr_cfg.get("enabled", True):
        return TriggerResult(triggered=False)

    cooldown_hours = config.get("cooldown_hours", 24)
    if _is_in_cooldown(state, "corrections_threshold", cooldown_hours):
        return TriggerResult(triggered=False, reason="cooldown")

    threshold = corr_cfg.get("threshold", 10)
    last_run = state.get("last_run_timestamp", "")

    # Count corrections since last evolve/reflect
    corrections_file = DATA_DIR / "corrections.jsonl"
    if not corrections_file.exists():
        return TriggerResult(triggered=False)

    count = 0
    skill_counts: dict[str, int] = {}
    for line in corrections_file.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            ts = rec.get("timestamp", "")
            if ts > last_run:
                count += 1
                skill = rec.get("last_skill", "")
                if skill:
                    skill_counts[skill] = skill_counts.get(skill, 0) + 1
        except json.JSONDecodeError:
            continue

    if count < threshold:
        return TriggerResult(triggered=False, details={"corrections_count": count})

    # Top 3 skills
    top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    skill_names = [s[0] for s in top_skills]

    # Per-skill Pre-flight ガードレール: correction 集中スキルへの能動警告
    from rl_common.config import load_user_config as _load_user_config
    _cfg = _load_user_config()
    per_skill_threshold = int(_cfg.get("correction_preflight_threshold", 3))
    preflight_skills = [
        skill for skill, cnt in skill_counts.items() if cnt >= per_skill_threshold
    ]

    if skill_names:
        action = f"/rl-anything:evolve-skill {skill_names[0]}"
        skill_list = ", ".join(skill_names)
        message = f"Corrections が {count} 件蓄積。関連スキル: {skill_list}。推奨: {action}"
    else:
        action = "/rl-anything:evolve"
        message = f"Corrections が {count} 件蓄積。推奨: {action}"

    if preflight_skills:
        preflight_list = ", ".join(preflight_skills)
        message += f"\n⚠️ Pre-flight 警告: {preflight_list} で {per_skill_threshold} 回以上の correction — `/rl-anything:evolve-skill <skill>` で自己進化パターンを組み込むことを推奨します"

    result = TriggerResult(
        triggered=True,
        reason="corrections_threshold",
        action=action,
        message=message,
        details={
            "corrections_count": count,
            "top_skills": skill_names,
            "per_skill_preflight": preflight_skills,
        },
    )

    state = _record_trigger(state, result)
    _save_state(state)
    return result
