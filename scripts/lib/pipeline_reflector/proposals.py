"""調整提案生成 + 永続化 + audit 用 Pipeline Health セクション生成。

Phase 12 / Slice 3 で `pipeline_reflector` package から切り出し（Phase 12 完了）。

注意: パス定数 (`DATA_DIR` / `PROPOSALS_FILE`) は `pipeline_reflector` パッケージ
(`__init__.py`) を Single Source of Truth として保持する。
本モジュールは関数呼び出し時に `pipeline_reflector` 名前空間から動的 lookup する
ことで `monkeypatch.setattr("pipeline_reflector.X", ...)` の効果を取り込む。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .outcomes import analyze_trajectory, load_outcomes, load_self_evolution_config


# --- 4.1: Adjustment proposal generation ---


def generate_adjustment_proposals(
    calibrations: dict[str, dict[str, Any]],
    control_chart: dict[str, dict[str, Any]],
    regression_result: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """confidence delta proposal + risk_level 判定。"""
    if config is None:
        config = load_self_evolution_config()

    proposals = []
    for it, cal in calibrations.items():
        delta = cal["calibrated"] - cal["current"]
        if abs(delta) < 0.01:
            continue  # 変化なし

        cc = control_chart.get(it, {})
        risk_level = cc.get("risk_level", "low")

        # Regression override
        if it in regression_result.get("regressions", {}):
            risk_level = "regression"

        proposal = {
            "issue_type": it,
            "current_confidence": cal["current"],
            "proposed_confidence": cal["calibrated"],
            "delta": round(delta, 4),
            "alpha": cal["alpha"],
            "risk_level": risk_level,
            "evidence": (
                f"approval_rate={cal['approval_rate']:.2f}, "
                f"sample_size={cal['sample_size']}, "
                f"α={cal['alpha']:.2f}"
            ),
        }

        if risk_level == "high":
            proposal["warning"] = "統計的に外れ値の調整幅です。慎重にレビューしてください。"
        elif risk_level == "regression":
            reg = regression_result["regressions"][it]
            proposal["warning"] = (
                f"回帰検出: {it} の false positive rate が "
                f"{reg['old_fp_rate']:.0%} → {reg['new_fp_rate']:.0%} に増加する可能性があります。"
            )

        proposals.append(proposal)

    return proposals


# --- 4.2: Proposal persistence ---


def record_proposal(proposal: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any] | None:
    """pipeline-proposals.jsonl に提案を記録する。"""
    if dry_run:
        return None
    import pipeline_reflector  # SoT: DATA_DIR / PROPOSALS_FILE 動的 lookup（monkeypatch 互換）
    record = {
        **proposal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    pipeline_reflector.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(pipeline_reflector.PROPOSALS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def update_proposal_status(issue_type: str, status: str) -> bool:
    """pipeline-proposals.jsonl 内の最新 pending 提案の status を更新する。"""
    import pipeline_reflector  # SoT: PROPOSALS_FILE 動的 lookup
    proposals_file = pipeline_reflector.PROPOSALS_FILE
    if not proposals_file.exists():
        return False
    lines = proposals_file.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines = []
    for line in reversed(lines):
        if not updated:
            try:
                rec = json.loads(line)
                if rec.get("issue_type") == issue_type and rec.get("status") == "pending":
                    rec["status"] = status
                    line = json.dumps(rec, ensure_ascii=False)
                    updated = True
            except json.JSONDecodeError:
                pass
        new_lines.append(line)
    new_lines.reverse()
    proposals_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


# --- 5.2: Pipeline Health section for audit ---


def build_pipeline_health_section(
    config: dict[str, Any] | None = None,
) -> list[str] | None:
    """audit レポート用の Pipeline Health セクションを生成する。LLM 不使用。"""
    if config is None:
        config = load_self_evolution_config()

    outcomes = load_outcomes(lookback_days=config.get("analysis_lookback_days", 30))
    min_outcomes = config["min_outcomes_for_analysis"]
    total = len(outcomes)

    lines = ["", "## Pipeline Health", ""]

    if total < min_outcomes:
        lines.append(
            f"データ不足（{total}/{min_outcomes} 件）。"
            "evolve を繰り返し実行してデータを蓄積してください。"
        )
        lines.append("")
        return lines

    analysis = analyze_trajectory(outcomes, config)
    by_type = analysis.get("by_type", {})
    degraded_threshold = config.get("approval_rate_degraded_threshold", 0.7)

    # Table header
    lines.append("| issue_type | total | precision | approval_rate | FP count | status |")
    lines.append("|---|---|---|---|---|---|")

    for it, stats in sorted(by_type.items()):
        t = stats.get("total", 0)
        prec = stats.get("precision", 0)
        ar = stats.get("approval_rate", 0)
        fp = stats.get("rejected", 0) + stats.get("skipped", 0)
        status = "DEGRADED" if ar < degraded_threshold else "OK"
        lines.append(f"| {it} | {t} | {prec:.0%} | {ar:.0%} | {fp} | {status} |")

    lines.append("")

    # DEGRADED recommendation
    degraded_types = [it for it, s in by_type.items() if s.get("approval_rate", 0) < degraded_threshold]
    if degraded_types:
        lines.append(
            f"DEGRADED: {', '.join(degraded_types)}。"
            "`/evolve-anything:evolve` での self-evolution を推奨します。"
        )
        lines.append("")

    return lines
