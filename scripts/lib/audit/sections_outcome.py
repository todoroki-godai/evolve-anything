"""アウトカム指標 v1 の observability セクション生成（#423, advisory）。

行動アウトカム3軸（correction 再発率 / 一発成功率 / rework 率）を audit に advisory 表示する。
スコア重みには入れない（2〜4 週並走 → 分布実測 → 重み昇格判断、ADR-046）。

各軸に evidence（件数・session_id 例など根拠レコードへの参照）を併記する
（learning_observability_quality_evidence_and_meaning 準拠）。データ不足の軸は沈黙でなく
「データ不足」を明示する（#393-#396）。検査対象は環境グローバルなストア（DATA_DIR）であり
project_dir には依存しないため、observability contract 互換で引数は受け取るだけ。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


_AXIS_LABELS = {
    "correction_recurrence": ("correction 再発率", "低いほど良い（同型修正の繰り返し減）"),
    "first_try_success": ("一発成功率", "高いほど良い（エラーなし完走）"),
    "rework": ("rework 率(近似)", "低いほど良い（検証なし連続編集の少なさ）"),
}


def _format_axis(key: str, axis: Dict[str, Any]) -> List[str]:
    label, direction = _AXIS_LABELS[key]
    value = axis.get("value")
    ev = axis.get("evidence", {})
    if value is None:
        reason = ev.get("reason", "no_data")
        store = ev.get("store", "")
        return [f"  ・{label}: データ不足（{reason} / {store}）"]

    lines = [f"  ・{label}: {value:.2f} — {direction}"]
    if key == "correction_recurrence":
        lines.append(
            f"      evidence: 窓内 correction {ev.get('records', 0)} 件 / "
            f"distinct type {ev.get('distinct_types', 0)} / "
            f"再発 type {ev.get('recurring_types', 0)}"
        )
        examples = ev.get("examples") or {}
        if examples:
            sample = ", ".join(f"{t}×{c}sess" for t, c in examples.items())
            lines.append(f"      再発 type 例: {sample}")
    elif key == "first_try_success":
        lines.append(
            f"      evidence: clean {ev.get('clean_sessions', 0)} / "
            f"total {ev.get('total_sessions', 0)} sessions"
        )
        examples = ev.get("examples") or []
        if examples:
            lines.append(f"      clean session 例: {', '.join(examples[:3])}")
    elif key == "rework":
        lines.append(
            f"      evidence: rework {ev.get('rework_sessions', 0)} / "
            f"編集あり {ev.get('total_sessions', 0)} sessions "
            f"(連続編集閾値 {ev.get('min_consecutive', '?')})"
        )
        examples = ev.get("examples") or []
        if examples:
            lines.append(f"      rework session 例: {', '.join(examples[:3])}")
    return lines


def build_outcome_metrics_section(project_dir: Path) -> Optional[List[str]]:
    """行動アウトカム3軸を audit に advisory 表示する（決定論・LLM 非依存）。

    観測可能性:
    - outcome_metrics モジュール未解決 → None（沈黙）
    - 3 軸とも no_data（DATA_DIR に該当ストアが 1 つも無い＝評価対象が無い環境）→ None（沈黙）
      orphan_store / hook_drift と同じ「評価対象が無ければ沈黙」の境界（silence != evaluated は
      評価対象がある場合にのみ適用）
    - いずれかの軸にデータがあれば 3 軸を出力（データ不足の軸は「データ不足」と明示）
    """
    try:
        from . import outcome_metrics
    except ImportError:
        return None

    metrics = outcome_metrics.compute_outcome_metrics(days=30)

    # 評価対象（該当ストア）が 1 つも無い環境は沈黙する。
    if all(metrics[k].get("value") is None for k in ("correction_recurrence", "first_try_success", "rework")):
        return None

    header = [
        "## Outcome Metrics v1 (advisory — スコア重みには未反映)",
        "",
        "行動アウトカムの目的変数（手戻り）を直接測る 3 軸。2〜4 週並走 → 分布実測 → "
        "重み昇格判断（ADR-046）。決定論・LLM 非依存。",
        "",
    ]
    body: List[str] = []
    for key in ("correction_recurrence", "first_try_success", "rework"):
        body.extend(_format_axis(key, metrics[key]))
    return header + body + [""]
