"""Read-time measurement metadata shared by audit-facing readers (#568).

No state is persisted: failures, dropped JSONL rows, scopes, and gate consistency are
derived while reading and carried to the renderer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from pillar2_metrics import PILLAR2_NOT_MEASURED_TARGETS


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


class MeasuredDict(dict):
    """A dict-compatible aggregate carrying the same read-time metadata."""

    def __init__(
        self,
        values: Optional[Dict[str, Any]] = None,
        *,
        measured: bool = True,
        reason: Optional[str] = None,
        dropped_lines: int = 0,
    ) -> None:
        super().__init__(values or {})
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


def _safe_text(value: Any) -> str:
    """Render diagnostic text without exposing the user's home directory."""
    text = str(value)
    home = str(Path.home())
    if home and text.startswith(home):
        return "~" + text[len(home):]
    return text.replace(home + "/", "~/") if home else text


def measurement_failure_reason(
    reader_name: str, path: Any, exc: BaseException
) -> str:
    """Build an actionable, home-safe reader failure reason."""
    resolved_path = path if path is not None else getattr(exc, "filename", None)
    return (
        f"読取失敗: {type(exc).__name__}"
        f"（reader={reader_name} / path={_safe_text(resolved_path or '(不明)')}"
        f" / detail={_safe_text(exc)}）"
    )


def read_measurement(
    reader: Callable[[], Any], *, fallback: Any, reader_name: Optional[str] = None
) -> tuple[Any, Dict[str, Any]]:
    """Fail-open reader wrapper that never turns an exception into a silent zero."""
    try:
        value = reader()
    except Exception as exc:
        return fallback, {
            "measured": False,
            "reason": measurement_failure_reason(
                reader_name or getattr(reader, "__qualname__", repr(reader)), None, exc
            ),
            "dropped_lines": 0,
        }
    return value, metadata(value)


def validate_correction_gate(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Recheck gate_open against its own formula without trusting the flag.

    ⚠️ **``summary`` を書き換えてはならない。** ``build_results_board`` は
    ``build_correction_rate_summary`` の返り値を verbatim で ``board["correction_rate"]``
    へ載せる契約で、``test_results_board.py::test_summary_is_passed_through_verbatim``
    が固定している。検算結果は返り値の health dict だけに載せ、表示側が
    ``gate_open_effective`` を見て系列表示の可否を決める。

    ⚠️ 検算に使うのは ``best_run_length`` であって ``current_run_length`` ではない。
    ``correction_rate._decide_display_gate`` の判定式は ``best_run >= k``（``:551``）で、
    ``current_run_length`` は #508 で追加された**点表示専用**のフィールド（同 ``:566-568``
    「gate_open の判定には使わない」）。ここで ``current`` を使うと、過去に4週連続が
    成立して以降途切れた状態（``best_run_length=4`` / ``current_run_length=1``）を
    「不一致」と誤判定し、#508 round2 [Must] が凍結した系列表示を黙って止めてしまう。

    柱3の**到達判定**（いま4週連続に達しているか）は ``current_run_length`` を見るのが
    正しいが、それは ``gate_open`` とは別の問いなので別フィールドで扱う（#568 の次段）。
    """
    gate = (summary or {}).get("gate")

    def _fail(reason: str) -> Dict[str, Any]:
        return {
            "measured": False,
            "reason": reason,
            "dropped_lines": 0,
            "reported_gate_open": bool(isinstance(gate, dict) and gate.get("gate_open") is True),
            "gate_open_effective": False,
        }

    if not isinstance(gate, dict):
        return _fail("gate schema がありません")
    if "required" not in gate or "best_run_length" not in gate:
        return _fail("gate 検算値がありません")

    required = gate.get("required")
    best_run = gate.get("best_run_length")
    reported = gate.get("gate_open") is True
    valid_numbers = (
        isinstance(required, int)
        and not isinstance(required, bool)
        and required > 0
        and isinstance(best_run, int)
        and not isinstance(best_run, bool)
        and best_run >= 0
    )
    if not valid_numbers:
        return _fail("gate 検算値が不正です")

    recomputed = best_run >= required
    if reported != recomputed:
        return _fail(
            "gate_open と最長連続週数が不一致"
            f"（reported={reported}・検算 {best_run}/{required}週）"
        )
    return {
        "measured": True,
        "reason": None,
        "dropped_lines": 0,
        "reported_gate_open": reported,
        "gate_open_effective": recomputed,
    }


def pillar_scopes(slug: str) -> Dict[str, Dict[str, Any]]:
    """The board measurements' intentionally different scopes."""
    project = {"kind": "project", "slug": slug, "label": f"当PJ: {slug}"}
    return {
        "capture_recall": {
            "kind": "local_untracked_eval_set",
            "label": (
                "ローカル評価セット（git 管理外・マシン依存。checkout 同梱または "
                "共有 DATA_DIR の bench/ に実体があれば測れる。実体を持たない"
                "マシンでは測定不能）"
            ),
        },
        "accepted_improvements": dict(project),
        "pillar2": {
            "kind": "project_and_global",
            "slug": slug,
            "label": f"当PJ: {slug} + グローバル反映",
        },
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
        correction_reader,
        fallback=correction_fallback,
        reader_name="correction_rate.build_correction_rate_summary",
    )
    # ⚠️ correction は verbatim のまま返す（上の docstring 参照）。
    gate_health = validate_correction_gate(correction)
    history, history_health = read_measurement(
        history_reader, fallback=[], reader_name="optimize_history_store.load_effective_history"
    )
    reverts, revert_health = read_measurement(
        revert_reader, fallback=[], reader_name="optimize_history_store.load_revert_events"
    )
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


def _pillar2_degraded_reason(pillar2: Dict[str, Any]) -> str:
    health = pillar2.get("health") or {}
    reasons: list[str] = []
    if health.get("snapshot_stable") is False:
        reasons.append("並行更新で snapshot が安定しません")
    if health.get("base_readable") is False:
        reasons.append("corrections 記録を読めません")
    if health.get("events_readable") is False:
        reasons.append("イベント記録を読めません")
    malformed = int(health.get("base_malformed_lines") or 0) + int(
        health.get("events_malformed_lines") or 0
    )
    if malformed:
        reasons.append(f"壊れた記録 {malformed} 行")
    for key, label in (
        ("orphan_events_unexpected", "孤立イベント"),
        ("unknown_schema_events", "未知 schema イベント"),
        ("invalid_events", "不正イベント"),
        ("duplicate_base_row_count", "重複 correction"),
        ("duplicate_event_row_count", "識別子が重複した反映記録"),
        ("orphan_confirmations", "孤立確認イベント"),
        ("duplicate_confirmations", "重複確認イベント"),
        ("hash_mismatch_count", "hash 不一致"),
    ):
        count = int(health.get(key) or 0)
        if count:
            reasons.append(f"{label} {count} 件")
    invalid_base_id_applied = int(
        health.get("invalid_base_id_applied_row_count") or 0
    )
    if invalid_base_id_applied:
        same_project = int(
            health.get("invalid_base_id_applied_same_project_row_count") or 0
        )
        global_looking = int(
            health.get("invalid_base_id_applied_global_looking_row_count") or 0
        )
        reasons.append(
            f"不正IDの反映済み基底 {invalid_base_id_applied} 件"
            f"（当PJ {same_project}・汎用扱い {global_looking}）"
        )
    legacy = int(pillar2.get("legacy_unverified_count") or 0)
    if legacy:
        reasons.append(f"未照合の旧記録 {legacy} 件")
    # 次の値は単独では測定不能にしない内訳・参考値だが、producer の health 契約を
    # 表示側が取りこぼしたときに fallback へ落ちないよう、単独入力にも説明を持たせる。
    if not reasons:
        for key, label in (
            ("orphan_events_expected", "制度開始前の対応先がない記録"),
            ("invalid_base_id_non_applied_row_count", "識別子を確認できない基底記録"),
            (
                "invalid_base_id_applied_same_project_row_count",
                "このプロジェクトに属する識別子不明の反映済み基底",
            ),
            (
                "invalid_base_id_applied_global_looking_row_count",
                "共通扱いの識別子不明の反映済み基底",
            ),
            (
                "invalid_base_id_non_applied_same_project_row_count",
                "このプロジェクトに属する識別子不明の基底記録",
            ),
            (
                "invalid_base_id_non_applied_global_looking_row_count",
                "共通扱いの識別子不明の基底記録",
            ),
        ):
            count = int(health.get(key) or 0)
            if count:
                reasons.append(f"{label} {count} 件")
    return "・".join(reasons) or "集計 health が degraded"


def _pillar2_not_measured_label(target: str, details: Dict[str, Any]) -> str:
    configured = PILLAR2_NOT_MEASURED_TARGETS.get(target, {})
    return configured.get("label", details.get("reason") or "理由不明")


def render_pillar2_health(
    pillar2: Dict[str, Any], measurements: Dict[str, Dict[str, Any]]
) -> list[str]:
    """柱2の件数・測定不能・未測定反映先を混同せず表示する。"""
    reader_health = measurements.get("pillar2")
    effective_reader_health = reader_health or {"measured": True}
    if not effective_reader_health.get("measured"):
        main = (
            "**実際に反映された改善（直近30日）: 測定不能"
            f"（{effective_reader_health.get('reason') or '理由不明'}）**"
        )
    elif not pillar2.get("measured"):
        main = (
            "**実際に反映された改善（直近30日）: 測定不能"
            f"（{_pillar2_degraded_reason(pillar2)}）**"
        )
    else:
        main = f"実際に反映された改善（直近30日）: {pillar2.get('count', 0)} 件"

    not_measured = pillar2.get("not_measured") or {}
    targets = [
        f"{target}（{_pillar2_not_measured_label(target, details)}）"
        for target, details in not_measured.items()
    ]
    pre_scheme_count = pillar2.get("pre_scheme_excluded_count")
    if reader_health is None or type(pre_scheme_count) is not int:
        pre_scheme_line = "新方式で記録を始める前の旧記録: 評価不能（除外件数も評価不能）"
    else:
        pre_scheme_line = (
            f"新方式で記録を始める前の旧記録: {pre_scheme_count}件"
            "（測定不能の理由からは除外）"
        )

    lines = [main, pre_scheme_line]
    if targets:
        lines.append(f"未測定の反映先: {' / '.join(targets)}")
    return lines


def render_revert_health(measurements: Dict[str, Dict[str, Any]]) -> list[str]:
    health = measurements.get("revert_events") or {"measured": True}
    if not health.get("measured"):
        return [f"取り下げ候補の戻し済み判定: 測定不能（{health.get('reason') or '理由不明'}）"]
    if health.get("dropped_lines"):
        return [f"取り下げ候補の戻し済み判定: {health.get('reason')}"]
    return []
