"""fan-out 費用対効果の observability セクション生成（#14, advisory）。

「複数 agent を fan-out すると本当に得か」を既存テレメトリから決定論で可視化する。
fitness の重み軸にはしない（outcome_metrics / memory_capability と同じ advisory レーン）。
arXiv 2606.13003（multi-agent fan-out の費用対効果）に対応。

構成（cost 先行・advantage はデータゲート付き）:
- cost（常時算出可能・非スパース）: fan-out session 率 / 平均 subagent / agent_type 内訳。
  token 単位の session join は未対応のため subagent 体数を cost proxy とし注記する。
- advantage（floor ゲート付き）: fan-out 群 vs single 群の一発成功率 delta。各群が floor 未満なら
  値を出さず「データ不足（サンプル不足）」を明示する（#15/#10 の構造的スパース性への対処）。

観測可能性契約（build_outcome_metrics_section と同契約）:
- fanout_cost モジュール未解決 → None（沈黙）
- 当 PJ の subagents が 0 件（評価対象なし）→ None（沈黙）
- 1 件以上 → ヘッダ + cost 行 + advantage 行（データ不足は明示）
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


def _format_cost(cost: Dict[str, Any]) -> List[str]:
    val = cost.get("value", {})
    ev = cost.get("evidence", {})
    rate = val.get("fanout_session_rate", 0.0)
    avg = val.get("avg_subagents_per_fanout_session", 0.0)
    lines = [
        f"  ・fan-out session 率: {rate:.2f} — 高いほど fan-out 多用",
        f"      evidence: fan-out {ev.get('fanout_sessions', 0)} / "
        f"spawning {ev.get('spawning_sessions', 0)} sessions "
        f"(subagent ≥2 を fan-out とみなす)",
        f"  ・fan-out session あたり平均 subagent: {avg:.2f} 体",
    ]
    breakdown = ev.get("agent_type_breakdown") or {}
    if breakdown:
        sample = ", ".join(f"{t}×{c}" for t, c in list(breakdown.items())[:5])
        lines.append(f"      agent_type 内訳: {sample}")
    lines.append(
        f"  ・cost proxy: subagent {ev.get('total_subagents', 0)} 体"
        f"（token 直接 join は未対応 = 体数を proxy）"
    )
    return lines


def _format_advantage(adv: Dict[str, Any]) -> List[str]:
    value = adv.get("value")
    ev = adv.get("evidence", {})
    if value is None:
        # 評価対象（subagents）はあるが各群の分母が floor 未満 → 沈黙でなくデータ不足明示。
        return [
            "  ・fan-out advantage（一発成功率 delta）: データ不足（サンプル不足）"
            f"— fan-out群 {ev.get('fanout_group_sessions', 0)} / "
            f"single群 {ev.get('single_group_sessions', 0)} "
            f"< floor {ev.get('floor', '?')}（同一タスク種別の対照比較は #15 同様スパース）",
        ]
    direction = "正なら fan-out が一発成功率で有利"
    return [
        f"  ・fan-out advantage（一発成功率 delta）: {value:+.2f} — {direction}",
        f"      evidence: fan-out群 {ev.get('fanout_success_rate', 0):.2f}"
        f"（{ev.get('fanout_group_sessions', 0)} sess） vs "
        f"single群 {ev.get('single_success_rate', 0):.2f}"
        f"（{ev.get('single_group_sessions', 0)} sess）",
    ]


def build_fanout_cost_section(project_dir: Path) -> Optional[List[str]]:
    """fan-out 費用対効果を audit に advisory 表示する（決定論・LLM 非依存）。

    - fanout_cost モジュール未解決 → None（沈黙）
    - 当 PJ の subagents が 0 件（評価対象なし）→ None（沈黙）
      （outcome_metrics / orphan_store と同じ「評価対象が無ければ沈黙」境界）
    - 1 件以上 → cost 行（常時算出）+ advantage 行（データ不足は明示）
    """
    try:
        import fanout_cost
    except ImportError:
        return None

    metrics = fanout_cost.compute_fanout_metrics(project_dir, days=30)
    if not metrics.get("applicable"):
        return None

    header = [
        "## Fan-out Cost/Advantage (当PJ・advisory — スコア重みには未反映)",
        "",
        "複数 agent を fan-out する費用対効果を既存テレメトリから測る（#14, arXiv 2606.13003）。"
        "cost は非スパースで常時算出。advantage は #15 同様スパースなので floor ゲート付き。"
        "決定論・LLM 非依存。",
        "",
    ]
    body: List[str] = []
    body.extend(_format_cost(metrics["cost"]))
    body.extend(_format_advantage(metrics["advantage"]))
    return header + body + [""]
