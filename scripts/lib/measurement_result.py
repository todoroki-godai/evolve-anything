"""Read-time measurement metadata shared by audit-facing readers (#568).

No state is persisted: failures, dropped JSONL rows, scopes, and gate consistency are
derived while reading and carried to the renderer.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Iterable, Optional


class MeasuredList(list):
    """A list-compatible reader result with explicit measurement metadata."""

    def __init__(
        self,
        values: Iterable[Any] = (),
        *,
        measured: bool = True,
        reason: Optional[str] = None,
        dropped_lines: int = 0,
    ) -> None:
        super().__init__(values)
        self.measured = measured
        self.reason = reason
        self.dropped_lines = dropped_lines


def metadata(value: Any) -> Dict[str, Any]:
    """Extract the common measured/reason/dropped_lines contract from a value."""
    return {
        "measured": bool(getattr(value, "measured", True)),
        "reason": getattr(value, "reason", None),
        "dropped_lines": int(getattr(value, "dropped_lines", 0)),
    }


def read_measurement(
    reader: Callable[[], Any], *, fallback: Any
) -> tuple[Any, Dict[str, Any]]:
    """Fail-open reader wrapper that never turns an exception into a silent zero."""
    try:
        value = reader()
    except Exception as exc:
        return fallback, {
            "measured": False,
            "reason": f"読取失敗: {type(exc).__name__}",
            "dropped_lines": 0,
        }
    return value, metadata(value)


def validate_correction_gate(summary: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Recheck gate_open against its own formula without trusting the flag.

    ⚠️ 検算に使うのは ``best_run_length`` であって ``current_run_length`` ではない。
    ``correction_rate._decide_display_gate`` の判定式は ``best_run >= k``（``:551``）で、
    ``current_run_length`` は #508 で追加された**点表示専用**のフィールド（同 ``:566-568``
    「gate_open の判定には使わない」）。ここで ``current`` を使うと、過去に4週連続が
    成立して以降途切れた状態（``best_run_length=4`` / ``current_run_length=1``）を
    「不一致」と誤判定し、#508 round2 [Must] が凍結した系列表示を黙って止めてしまう。

    柱3の**到達判定**（いま4週連続に達しているか）は ``current_run_length`` を見るのが
    正しいが、それは ``gate_open`` とは別の問いなので別フィールドで扱う（#568 の次段）。
    """
    normalized = deepcopy(summary)
    gate = normalized.get("gate")
    if not isinstance(gate, dict):
        return normalized, {
            "measured": False,
            "reason": "gate schema がありません",
            "dropped_lines": 0,
        }
    if "required" not in gate or "best_run_length" not in gate:
        return normalized, {
            "measured": False,
            "reason": "gate 検算値がありません",
            "dropped_lines": 0,
        }
    required = gate.get("required")
    current = gate.get("best_run_length")
    reported = gate.get("gate_open") is True
    valid_numbers = (
        isinstance(required, int)
        and not isinstance(required, bool)
        and required > 0
        and isinstance(current, int)
        and not isinstance(current, bool)
        and current >= 0
    )
    recomputed = valid_numbers and current >= required
    gate["reported_gate_open"] = reported
    if not valid_numbers:
        gate["gate_open"] = False
        return normalized, {
            "measured": False,
            "reason": "gate 検算値が不正です",
            "dropped_lines": 0,
        }
    if reported != recomputed:
        gate["gate_open"] = False
        return normalized, {
            "measured": False,
            "reason": (
                "gate_open と最長連続週数が不一致"
                f"（reported={reported}・検算 {current}/{required}週）"
            ),
            "dropped_lines": 0,
        }
    gate["gate_open"] = recomputed
    return normalized, {"measured": True, "reason": None, "dropped_lines": 0}


def pillar_scopes(slug: str) -> Dict[str, Dict[str, Any]]:
    """The four board pillars' intentionally different measurement scopes."""
    project = {"kind": "project", "slug": slug, "label": f"当PJ: {slug}"}
    return {
        "capture_recall": {
            "kind": "plugin_bundled_eval_set",
            "label": "プラグイン同梱評価セット",
        },
        "accepted_improvements": dict(project),
        "correction_rate": {"kind": "all_projects", "label": "全PJ合算"},
        "withdrawal_candidates": dict(project),
    }


def collect_board_measurements(
    slug: str,
    *,
    correction_reader: Callable[[], Dict[str, Any]],
    history_reader: Callable[[], Any],
    revert_reader: Callable[[], Any],
    correction_fallback: Dict[str, Any],
) -> tuple[Dict[str, Any], Any, Any, Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Read all board sources fail-open and keep each source's health separate."""
    correction, correction_health = read_measurement(
        correction_reader, fallback=correction_fallback
    )
    correction, gate_health = validate_correction_gate(correction)
    history, history_health = read_measurement(history_reader, fallback=[])
    reverts, revert_health = read_measurement(revert_reader, fallback=[])
    return (
        correction,
        [] if history is None else history,
        [] if reverts is None else reverts,
        pillar_scopes(slug),
        {
            "correction_rate": correction_health,
            "correction_rate_gate": gate_health,
            "decisions": history_health,
            "revert_events": revert_health,
        },
    )


def render_scope(scopes: Dict[str, Dict[str, Any]], pillar: str) -> str:
    return f"測定スコープ: {scopes[pillar]['label']}"


def render_rate_health(measurements: Dict[str, Dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for key, label in (
        ("correction_rate", "指摘率"),
        ("correction_rate_gate", "指摘率ゲート"),
    ):
        health = measurements.get(key) or {"measured": True}
        if not health.get("measured"):
            lines.append(f"**{label}: 測定不能（{health.get('reason') or '理由不明'}）**")
    return lines


def render_decisions_health(
    decisions: Dict[str, int], measurements: Dict[str, Dict[str, Any]]
) -> list[str]:
    health = measurements.get("decisions") or {"measured": True}
    if health.get("measured"):
        lines = [
            f"採用した改善（直近30日）: accepted {decisions['accepted']} 件 / "
            f"rejected {decisions['rejected']} 件 / pending {decisions['pending']} 件 / "
            f"excluded {decisions['excluded']} 件"
        ]
    else:
        lines = [f"採用した改善（直近30日）: 測定不能（{health.get('reason') or '理由不明'}）"]
    if health.get("dropped_lines"):
        lines.append(f"  {health.get('reason')}")
    return lines


def render_revert_health(measurements: Dict[str, Dict[str, Any]]) -> list[str]:
    health = measurements.get("revert_events") or {"measured": True}
    if not health.get("measured"):
        return [f"取り下げ候補の戻し済み判定: 測定不能（{health.get('reason') or '理由不明'}）"]
    if health.get("dropped_lines"):
        return [f"取り下げ候補の戻し済み判定: {health.get('reason')}"]
    return []
