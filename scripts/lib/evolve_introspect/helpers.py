"""evolve_introspect.helpers — 検出器と orchestration が共有する低レベルヘルパ（#122-P5）。

detectors と __init__（reconcile）の両方から使われる純粋関数を集約する。
leaf モジュール（パッケージ内の他モジュールに依存しない）＝循環 import を避ける。
"""
from __future__ import annotations

from typing import Any, Dict


def _section(candidates, zero_line, hit_template, name_of) -> Dict[str, Any]:
    if not candidates:
        return {"candidates": [], "summary_line": zero_line}
    names = ", ".join(name_of(c) for c in candidates[:5])
    if len(candidates) > 5:
        names += f", 他 {len(candidates) - 5} 件"
    return {
        "candidates": candidates,
        "summary_line": hit_template.format(n=len(candidates), names=names),
    }


def _skill_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("skill_name", "skill", "name"):
            val = entry.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _issue_file(issue: Dict[str, Any]) -> str:
    for key in ("file", "filename", "target", "path"):
        val = issue.get(key)
        if isinstance(val, str) and val:
            return val
    return ""
