"""アウトカム指標 v1 の observability セクション生成（#423, advisory）。

行動アウトカム3軸（correction 再発率 / 一発成功率 / rework 率）を audit に advisory 表示する。
スコア重みには入れない（2〜4 週並走 → 分布実測 → 重み昇格判断、ADR-046）。

各軸に evidence（件数・session_id 例など根拠レコードへの参照）を併記する
（learning_observability_quality_evidence_and_meaning 準拠）。データ不足の軸は沈黙でなく
「データ不足」を明示する（#393-#396）。

スコープ（#489）: ストア（corrections/sessions）は全PJ共通だが、当PJレポートの数値は
project_dir の basename を当PJ識別子として当PJスコープに直す（全PJ集計の無ラベル表示で
読み手が当PJ値と誤認するのを防ぐ。一発成功率 全PJ0.73 vs 当PJ0.88 の 15pt 乖離を実測）。
全PJ横断の重み昇格判断は outcome_promotion_readiness が per-PJ 分解で別途担う（本 builder の
当PJ化はその入力に影響しない）。
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
        # #529-2: 最小分母 floor 未満は「データ不足」でなく「サンプル不足」と
        # 区別して表示する（ストアはあるが率を出すには分母が小さい状態）。
        if reason == "insufficient_sample":
            return [
                f"  ・{label}: サンプル不足"
                f"（distinct {ev.get('distinct_types', 0)} type"
                f" < floor {ev.get('floor', '?')}）— 率は非表示"
            ]
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

    # #489: 当PJスコープに直す。project_dir を worktree 安全 slug に正規化して渡す
    # （本体 / worktree どちらから audit しても同じ slug になり取りこぼしを防ぐ）。
    metrics = outcome_metrics.compute_outcome_metrics(
        days=30, project=outcome_metrics._normalize_pj(str(project_dir))
    )

    # 評価対象（該当ストア）が 1 つも無い環境は沈黙する。
    if all(metrics[k].get("value") is None for k in ("correction_recurrence", "first_try_success", "rework")):
        return None

    header = [
        "## Outcome Metrics v1 (当PJ・advisory — スコア重みには未反映)",
        "",
        "行動アウトカムの目的変数（手戻り）を直接測る 3 軸（当PJスコープ, #489）。"
        "2〜4 週並走 → 分布実測 → 重み昇格判断（ADR-046）。決定論・LLM 非依存。",
        "",
    ]
    body: List[str] = []
    for key in ("correction_recurrence", "first_try_success", "rework"):
        body.extend(_format_axis(key, metrics[key]))
    return header + body + [""]
