"""Self-evolution トリガー評価。

remediation-outcomes.jsonl の false positive 蓄積 (`_evaluate_self_evolution`) と
承認率の継続的低下 (`_evaluate_approval_rate_decline`) を評価する。

注: `DATA_DIR` は `from . import DATA_DIR` 関数内 lazy lookup
（テストの `mock.patch("trigger_engine.DATA_DIR")` 追従）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .state import (
    TriggerResult,
    _is_in_cooldown,
    _load_state,
    _record_trigger,
    _save_state,
    load_trigger_config,
)


def get_rejected_stats(skill_name: str) -> dict[str, Any]:
    """指定スキルの evolve 提案に対する rejected 統計を返す。

    remediation-outcomes.jsonl の skill_evolve_candidate 種別で、
    file フィールドにスキル名を含むレコードを集計する。

    Returns:
        {"rejected_count": int, "total_count": int, "rejected_rate": float}
        jsonl が存在しない場合: {"rejected_count": 0, "total_count": 0, "rejected_rate": 0.0}
    """
    from . import DATA_DIR  # noqa: PLC0415

    _empty: dict[str, Any] = {"rejected_count": 0, "total_count": 0, "rejected_rate": 0.0}

    outcomes_file = DATA_DIR / "remediation-outcomes.jsonl"
    if not outcomes_file.exists():
        return _empty

    total = 0
    rejected = 0
    try:
        for line in outcomes_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("issue_type") != "skill_evolve_candidate":
                continue
            # file フィールドにスキル名が含まれるか確認（パス境界で一致）
            file_field = rec.get("file", "")
            if f"/skills/{skill_name}/" not in file_field and not file_field.endswith(f"/skills/{skill_name}/SKILL.md"):
                continue
            total += 1
            decision = rec.get("user_decision", rec.get("result", ""))
            if decision == "rejected":
                rejected += 1
    except OSError:
        return _empty

    rate = rejected / total if total > 0 else 0.0
    return {"rejected_count": rejected, "total_count": total, "rejected_rate": rate}


def _evaluate_self_evolution(state: dict[str, Any] | None = None) -> TriggerResult:
    """False positive 蓄積に基づく self-evolution トリガーを評価する。"""
    from . import DATA_DIR

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
    from . import DATA_DIR

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
