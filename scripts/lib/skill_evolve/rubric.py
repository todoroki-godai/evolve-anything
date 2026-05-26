"""step-level rubric チェックポイント (Co-ReAct 論文 inspired)。

evolve_skill_proposal() / apply_evolve_proposal() 実行時に
既存チェック（pitfalls/pre-flight/trigger/diff_budget）の結果を
stdout_lines として可視化する。
"""
from __future__ import annotations

from typing import Any

DIFF_BUDGET = 30  # lines


def rubric_checkpoint(phase: str, proposal_dict: dict[str, Any]) -> dict[str, Any]:
    """evolve-skill の propose/apply フェーズで既存チェック結果を可視化する。

    Args:
        phase: "propose" | "apply"
        proposal_dict:
            propose: evolve_skill_proposal() の戻り値
                     (keys: pitfalls_template, sections_to_add, diff_lines, ...)
            apply:   evolve_skill_proposal() の戻り値 (correction_ids を含む)

    Returns:
        {
            "phase": str,
            "checks": [{"name": str, "passed": bool, ...}],
            "stdout_lines": [str],
        }
    """
    checks: list[dict[str, Any]] = []

    if phase == "propose":
        # pitfalls: pitfalls_template キーが存在し空でないか
        checks.append({
            "name": "pitfalls",
            "passed": bool(proposal_dict.get("pitfalls_template")),
        })

        # pre_flight: sections_to_add に "Pre-flight" セクションが含まれるか
        sections = proposal_dict.get("sections_to_add", "")
        checks.append({
            "name": "pre_flight",
            "passed": "Pre-flight" in sections,
        })

        # trigger: sections_to_add に "Failure-triggered" セクションが含まれるか
        checks.append({
            "name": "trigger",
            "passed": "Failure-triggered" in sections,
        })

        # diff_budget: proposal 内の diff_lines が DIFF_BUDGET 以下か
        # (diff_lines は evolve_skill_proposal() が count_diff_lines で計算して渡す)
        actual_lines = proposal_dict.get("diff_lines", 0)
        checks.append({
            "name": "diff_budget",
            "passed": actual_lines <= DIFF_BUDGET,
            "actual": actual_lines,
        })

    elif phase == "apply":
        # reason_refs: correction_ids が存在し空でないか
        correction_ids = proposal_dict.get("correction_ids", [])
        checks.append({
            "name": "reason_refs",
            "passed": bool(correction_ids),
        })

    # stdout_lines 組み立て
    stdout_lines: list[str] = [f"├── [rubric] {phase}:"]
    for check in checks:
        name = check["name"]
        passed = check["passed"]
        mark = "✔" if passed else "✘"
        status = "present" if passed else "missing"

        if name == "diff_budget":
            actual = check.get("actual", 0)
            value_str = f"{mark} {actual}/{DIFF_BUDGET}"
            stdout_lines.append(f"│     {name}:{' ' * (12 - len(name))}{value_str}")
        else:
            stdout_lines.append(f"│     {name}:{' ' * (12 - len(name))}{mark} {status}")

    return {
        "phase": phase,
        "checks": checks,
        "stdout_lines": stdout_lines,
    }
