"""#401: daily runner's read-only weekly board snapshot calculation."""
import json
from datetime import date, datetime
from pathlib import Path

import correction_rate
import evolve_revert_listing
import pillar2_metrics
import pj_slug
from measurement_result import read_measurement


def build_weekly_board(queue_path: Path, project_root: Path, *, now=None) -> dict:
    """Preserve a valid same-week snapshot; retry malformed or unmeasured data."""
    now = now if now is not None else datetime.now().astimezone()
    previous = None
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        previous = queue.get("weekly_board") if isinstance(queue, dict) else None
    except (OSError, ValueError):
        pass  # Missing/unreadable old snapshots are recomputed, never used as measurements.

    previous_week = None
    if (isinstance(previous, dict) and previous.get("measured") is not False
            and isinstance(previous.get("week_id"), str)
            and isinstance(previous.get("computed_on"), str)):
        try:
            if date.fromisoformat(previous["computed_on"]).isoformat() == previous["computed_on"]:
                previous_week = previous["week_id"]
        except ValueError:
            pass
    week_id = correction_rate.week_id_for(now)
    if previous_week == week_id:
        return previous

    def measure():
        pillar2, health = read_measurement(
            lambda: pillar2_metrics.count_applied_reflections(project_root, now=now),
            fallback={}, reader_name="pillar2_metrics.count_applied_reflections",
        )
        if health["measured"] is False or pillar2.get("measured") is False:
            raise ValueError(health.get("reason") or "柱2の集計 health が degraded")
        correction = correction_rate.build_correction_rate_summary(now=now)
        if correction.measured is False:
            raise ValueError(correction.reason or "指摘率の読取障害")
        items = evolve_revert_listing.build_revert_listing(pj_slug.resolve_pj_slug(project_root))
        if items.measured is False:
            raise ValueError(items.reason or "戻せる採用の読取障害")
        return {
            "week_id": week_id, "computed_on": now.date().isoformat(), "measured": True,
            "pillar2_count": pillar2["count"],
            "point_week": correction["gate"]["point_week"],
            "pillar4_count": sum(1 for it in items
                                 if it["revert_available"] and not it.get("subsequent_change")),
        }

    result, health = read_measurement(measure, fallback=None, reader_name="weekly_board")
    if health["measured"] is False:
        return {"measured": False, "reason": health["reason"], "generated_at": now.isoformat()}
    return result
