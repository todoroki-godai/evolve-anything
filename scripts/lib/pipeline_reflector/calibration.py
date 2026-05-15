"""Confidence キャリブレーション + 統計的管理図 + 回帰チェック。

Phase 12 / Slice 2 で `pipeline_reflector` package から切り出し。

注意: パス定数 (`DATA_DIR` / `CALIBRATION_FILE`) は `pipeline_reflector` パッケージ
(`__init__.py`) を Single Source of Truth として保持する。
本モジュールは関数呼び出し時に `pipeline_reflector` 名前空間から動的 lookup する
ことで `monkeypatch.setattr("pipeline_reflector.X", ...)` の効果を取り込む。
"""
from __future__ import annotations

import json
import statistics
from typing import Any

from .outcomes import load_outcomes, load_self_evolution_config


# --- 3.1: EWA Calibration ---


def calibrate_confidence(
    outcomes: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """issue_type 別の EWA キャリブレーションを算出する。

    Returns:
        {"calibrations": {issue_type: {current, calibrated, alpha, sample_size, approval_rate}}}
    """
    if config is None:
        config = load_self_evolution_config()
    if outcomes is None:
        lookback = config.get("analysis_lookback_days", 30)
        outcomes = load_outcomes(lookback_days=lookback)

    min_per_type = config["min_outcomes_per_type"]
    sample_threshold = config["calibration_sample_threshold"]
    max_alpha = config["max_calibration_alpha"]

    # Group by issue_type
    by_type: dict[str, list[dict[str, Any]]] = {}
    for rec in outcomes:
        it = rec.get("issue_type", "unknown")
        if it not in by_type:
            by_type[it] = []
        by_type[it].append(rec)

    calibrations: dict[str, dict[str, Any]] = {}
    for it, recs in by_type.items():
        if len(recs) < min_per_type:
            continue

        # Observed approval rate
        approved = sum(
            1 for r in recs
            if r.get("user_decision") == "approved" or r.get("result") == "success"
        )
        observed_rate = approved / len(recs)

        # Current confidence (average from records)
        confidences = [r.get("confidence_score", 0.5) for r in recs]
        current = statistics.mean(confidences) if confidences else 0.5

        # EWA: α = min(sample_size / threshold, max_alpha)
        alpha = min(len(recs) / sample_threshold, max_alpha)
        calibrated = alpha * observed_rate + (1 - alpha) * current

        calibrations[it] = {
            "current": round(current, 4),
            "calibrated": round(calibrated, 4),
            "alpha": round(alpha, 4),
            "sample_size": len(recs),
            "approval_rate": round(observed_rate, 4),
        }

    return {"calibrations": calibrations}


# --- 3.2: Calibration file I/O ---


def load_calibration() -> dict[str, Any]:
    """confidence-calibration.json を読み込む。"""
    import pipeline_reflector  # SoT: CALIBRATION_FILE を動的 lookup（monkeypatch 互換）
    cal_file = pipeline_reflector.CALIBRATION_FILE
    if not cal_file.exists():
        return {}
    try:
        return json.loads(cal_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_calibration(data: dict[str, Any]) -> None:
    """confidence-calibration.json に保存する。"""
    import pipeline_reflector  # SoT: DATA_DIR / CALIBRATION_FILE を動的 lookup
    pipeline_reflector.DATA_DIR.mkdir(parents=True, exist_ok=True)
    pipeline_reflector.CALIBRATION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- 3.3: Control chart check ---


def check_control_chart(
    calibrations: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """μ ± 2σ 範囲外の delta に risk_level: "high" を付与する。

    Returns:
        {issue_type: {delta, mean_delta, std_delta, risk_level}}
    """
    deltas = []
    for it, cal in calibrations.items():
        delta = cal["calibrated"] - cal["current"]
        deltas.append((it, delta))

    if len(deltas) < 2:
        # 2件未満では標準偏差を計算できない → 全て low
        return {
            it: {"delta": d, "mean_delta": d, "std_delta": 0.0, "risk_level": "low"}
            for it, d in deltas
        }

    delta_values = [d for _, d in deltas]
    mean_d = statistics.mean(delta_values)
    std_d = statistics.stdev(delta_values)

    result = {}
    for it, d in deltas:
        if std_d > 0 and abs(d - mean_d) > 2 * std_d:
            risk = "high"
        else:
            risk = "low"
        result[it] = {
            "delta": round(d, 4),
            "mean_delta": round(mean_d, 4),
            "std_delta": round(std_d, 4),
            "risk_level": risk,
        }
    return result


# --- 3.4: Regression check ---


def check_calibration_regression(
    calibrations: dict[str, dict[str, Any]],
    outcomes: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """変更後 confidence で既存 outcomes を再分類し回帰検出。

    Returns:
        {"has_regression": bool, "regressions": {issue_type: {old_fp_rate, new_fp_rate, increase}}}
    """
    if config is None:
        config = load_self_evolution_config()
    fp_threshold = config.get("regression_fp_increase_threshold", 0.1)

    AUTO_FIX_CONFIDENCE = 0.9

    # Current FP rates by type
    by_type: dict[str, list[dict[str, Any]]] = {}
    for rec in outcomes:
        it = rec.get("issue_type", "unknown")
        if it not in by_type:
            by_type[it] = []
        by_type[it].append(rec)

    regressions: dict[str, dict[str, Any]] = {}
    for it, recs in by_type.items():
        if not recs:
            continue

        # Current classification-based FP: rejected/skipped among auto_fixable
        current_auto = [r for r in recs if r.get("category") == "auto_fixable"]
        current_fp = sum(
            1 for r in current_auto
            if r.get("user_decision") in ("rejected", "skipped")
        )
        current_fp_rate = current_fp / len(current_auto) if current_auto else 0.0

        # Re-classify with calibrated confidence
        cal = calibrations.get(it)
        if cal is None:
            continue
        new_confidence = cal["calibrated"]

        # Simulate: if new_confidence >= AUTO_FIX, more items become auto_fixable
        # If new_confidence < AUTO_FIX, fewer items are auto_fixable → FP should decrease
        # Check if this change causes other types to have more FP
        # Simplified: check if lowering confidence changes auto_fixable classification
        new_auto = [
            r for r in recs
            if new_confidence >= AUTO_FIX_CONFIDENCE
            and r.get("impact_scope", "file") in ("file", "project")
        ]
        if not new_auto:
            continue
        new_fp = sum(
            1 for r in new_auto
            if r.get("user_decision") in ("rejected", "skipped")
        )
        new_fp_rate = new_fp / len(new_auto) if new_auto else 0.0

        increase = new_fp_rate - current_fp_rate
        if increase >= fp_threshold:
            regressions[it] = {
                "old_fp_rate": round(current_fp_rate, 4),
                "new_fp_rate": round(new_fp_rate, 4),
                "increase": round(increase, 4),
            }

    return {
        "has_regression": len(regressions) > 0,
        "regressions": regressions,
    }
