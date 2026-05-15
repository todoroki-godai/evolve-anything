"""Outcome 取り込み + 行動軌跡分析 + false-positive 検出 + 自然言語診断。

Phase 12 / Slice 1 で `pipeline_reflector` package から切り出し。

注意: `DATA_DIR` / `OUTCOMES_FILE` 等のパス定数は `pipeline_reflector` パッケージ
(`__init__.py`) に保持され、テストは `monkeypatch.setattr("pipeline_reflector.X", ...)`
でパッチする。本モジュールは関数呼び出し時に `pipeline_reflector` 名前空間から
動的に lookup することで monkeypatch の効果を取り込む。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


# --- D6: Default self-evolution config ---

DEFAULT_SELF_EVOLUTION_CONFIG: dict[str, Any] = {
    "min_outcomes_for_analysis": 20,
    "min_outcomes_per_type": 10,
    "calibration_sample_threshold": 30,
    "max_calibration_alpha": 0.7,
    "false_positive_rate_threshold": 0.3,
    "approval_rate_healthy_threshold": 0.8,
    "approval_rate_degraded_threshold": 0.7,
    "approval_rate_decline_threshold": 0.2,
    "self_evolution_cooldown_hours": 72,
    "decline_sample_size": 10,
    "regression_fp_increase_threshold": 0.1,
    "analysis_lookback_days": 30,
    "systematic_rejection_threshold": 3,
    "minor_line_excess": 2,
}


def load_self_evolution_config(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """self_evolution config を evolve-state.json から読み込み、デフォルトとマージして返す。

    trigger_engine.py の load_trigger_config() パターン準拠。
    """
    if state is None:
        state = _load_state()
    user_config = state.get("trigger_config", {}).get("self_evolution", {})
    config = dict(DEFAULT_SELF_EVOLUTION_CONFIG)
    config.update(user_config)
    return config


def _load_state() -> dict[str, Any]:
    """evolve-state.json を読み込む。"""
    import pipeline_reflector  # SoT: パッケージ __init__.py 上の DATA_DIR を動的参照
    state_file = pipeline_reflector.DATA_DIR / "evolve-state.json"
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# --- Outcome loading ---


def load_outcomes(*, lookback_days: int | None = None) -> list[dict[str, Any]]:
    """remediation-outcomes.jsonl を読み込む。"""
    import pipeline_reflector  # SoT: OUTCOMES_FILE 動的参照（monkeypatch 互換）
    outcomes_file = pipeline_reflector.OUTCOMES_FILE
    if not outcomes_file.exists():
        return []
    records = []
    cutoff = None
    if lookback_days is not None:
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff_dt = now - timedelta(days=lookback_days)
        cutoff = cutoff_dt.isoformat()
    for line in outcomes_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if cutoff and rec.get("timestamp", "") < cutoff:
                continue
            records.append(rec)
        except json.JSONDecodeError:
            continue
    return records


# --- 2.1: Trajectory Analysis ---


def analyze_trajectory(
    outcomes: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """remediation-outcomes を issue_type 別に集計し、パイプラインの弱点を分析する。

    Returns:
        {"sufficient": bool, "total": int, "by_type": {issue_type: {...}}, "diagnosis": str}
    """
    if config is None:
        config = load_self_evolution_config()
    if outcomes is None:
        lookback = config.get("analysis_lookback_days", 30)
        outcomes = load_outcomes(lookback_days=lookback)

    min_outcomes = config["min_outcomes_for_analysis"]
    total = len(outcomes)

    if total < min_outcomes:
        return {
            "sufficient": False,
            "total": total,
            "min_required": min_outcomes,
            "by_type": {},
            "diagnosis": f"データ不足（{total}/{min_outcomes} 件）",
        }

    by_type: dict[str, dict[str, Any]] = {}
    for rec in outcomes:
        it = rec.get("issue_type", "unknown")
        if it not in by_type:
            by_type[it] = {"total": 0, "approved": 0, "rejected": 0, "skipped": 0, "fix_failed": 0}
        by_type[it]["total"] += 1
        decision = rec.get("user_decision", rec.get("result", ""))
        if decision == "approved" or rec.get("result") == "success":
            by_type[it]["approved"] += 1
        elif decision == "rejected" or rec.get("result") == "rejected":
            by_type[it]["rejected"] += 1
        elif decision == "skipped" or rec.get("result") == "skipped":
            by_type[it]["skipped"] += 1
        if rec.get("result") == "fix_failed":
            by_type[it]["fix_failed"] += 1

    # Compute metrics
    for it, stats in by_type.items():
        t = stats["total"]
        if t > 0:
            stats["approval_rate"] = stats["approved"] / t
            stats["false_positive_rate"] = (stats["rejected"] + stats["skipped"]) / t
            stats["precision"] = stats["approved"] / t
        else:
            stats["approval_rate"] = 0.0
            stats["false_positive_rate"] = 0.0
            stats["precision"] = 0.0

    # Generate diagnosis
    diagnosis = _generate_diagnosis(by_type, config)

    return {
        "sufficient": True,
        "total": total,
        "min_required": min_outcomes,
        "by_type": by_type,
        "diagnosis": diagnosis,
    }


# --- 2.2: False positive detection ---


def detect_false_positives(
    outcomes: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """High-confidence rejection と systematic rejection パターンを検出する。"""
    if config is None:
        config = load_self_evolution_config()

    high_confidence_rejections: list[dict[str, Any]] = []
    systematic_rejections: dict[str, list[dict[str, Any]]] = {}
    threshold = config.get("systematic_rejection_threshold", 3)

    for rec in outcomes:
        confidence = rec.get("confidence_score", 0.0)
        decision = rec.get("user_decision", rec.get("result", ""))
        it = rec.get("issue_type", "unknown")
        category = rec.get("category", "")

        # High-confidence rejection
        if confidence >= 0.9 and decision in ("rejected", "skipped"):
            high_confidence_rejections.append(rec)

        # Systematic rejection tracking (proposable category)
        if category == "proposable" and decision in ("rejected", "skipped"):
            if it not in systematic_rejections:
                systematic_rejections[it] = []
            systematic_rejections[it].append(rec)

    # Check for consecutive systematic rejections
    systematic_flags: dict[str, int] = {}
    for it, recs in systematic_rejections.items():
        # Count consecutive rejections from the end
        consecutive = 0
        for rec in reversed(recs):
            if rec.get("user_decision", rec.get("result", "")) in ("rejected", "skipped"):
                consecutive += 1
            else:
                break
        if consecutive >= threshold:
            systematic_flags[it] = consecutive

    return {
        "high_confidence_rejections": high_confidence_rejections,
        "systematic_rejections": systematic_flags,
    }


# --- 2.3: Natural language diagnosis ---


def _generate_diagnosis(
    by_type: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> str:
    """分析結果をもとに自然言語診断を生成する。"""
    fp_threshold = config.get("false_positive_rate_threshold", 0.3)
    healthy_threshold = config.get("approval_rate_healthy_threshold", 0.8)

    problematic: list[str] = []
    for it, stats in by_type.items():
        if stats.get("false_positive_rate", 0) >= fp_threshold:
            rate = stats.get("approval_rate", 0)
            problematic.append(
                f"{it} の confidence_score が過大評価されています。"
                f"実績 approval_rate: {rate:.0%}。confidence の引き下げを推奨します。"
            )

    if not problematic:
        all_healthy = all(
            stats.get("approval_rate", 0) >= healthy_threshold
            for stats in by_type.values()
            if stats.get("total", 0) > 0
        )
        if all_healthy:
            return (
                "パイプラインは健全です。全 issue_type で承認率 "
                f"{healthy_threshold:.0%} 以上を維持しています。"
            )
        return "パイプラインは概ね健全です。"

    return "\n".join(problematic)
