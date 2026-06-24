"""subagent_traces.query — agent_type 別の軌跡サマリ集計（#38）。

read_traces（read-only 純度）の結果を agent_type 単位に集計し、
内部「一発成功率」と平均 tool error を返す。``n >= min_traces`` の floor ゲートで
サンプル不足のノイズを抑制。空 / ID 形 agent_type は除外（is_noise_agent_type 単一ソース）。
決定論・ゼロ LLM。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import store as _store

# floor の既定値。これ未満の agent_type は集計に出さない（fanout_cost の floor 思想に倣う）。
DEFAULT_MIN_TRACES = 3


def per_agent_type_summary(
    slug: str,
    *,
    min_traces: int = DEFAULT_MIN_TRACES,
    data_dir: Optional[Path] = None,
) -> List[Dict]:
    """agent_type 別に内部一発成功率と平均 tool error を集計する（floor ゲート付き）。

    Returns（agent_type のアルファベット順）:
        [{"agent_type": str, "n": int,
          "first_try_success_rate": float, "avg_tool_error": float}, ...]
    n < min_traces の agent_type は除外。空 / ID 形 agent_type は除外。
    """
    try:
        from rl_common import is_noise_agent_type
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        def is_noise_agent_type(at):  # type: ignore
            return not str(at or "").strip()

    traces = _store.read_traces(slug, data_dir=data_dir)

    buckets: Dict[str, Dict[str, float]] = {}
    for rec in traces.values():
        at = rec.get("agent_type", "")
        if is_noise_agent_type(at):
            continue
        b = buckets.setdefault(at, {"n": 0, "success": 0, "tool_error_sum": 0})
        b["n"] += 1
        if rec.get("first_try_success") is True:
            b["success"] += 1
        b["tool_error_sum"] += int(rec.get("tool_error_count", 0) or 0)

    out: List[Dict] = []
    for at in sorted(buckets):
        b = buckets[at]
        n = int(b["n"])
        if n < min_traces:
            continue
        out.append(
            {
                "agent_type": at,
                "n": n,
                "first_try_success_rate": round(b["success"] / n, 4),
                "avg_tool_error": round(b["tool_error_sum"] / n, 4),
            }
        )
    return out
