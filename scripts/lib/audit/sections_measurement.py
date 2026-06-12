"""measurement_bug メタ検査の observability セクション生成（#445, advisory）。

複数 PJ の集計値（env_score / issues_total）が bit-exact 一致したら測定バグ候補として
audit/evolve に advisory surface する。スコア重みには入れない（learning_measurement_layer_diagnosis）。

検査対象は環境グローバルなストア（DATA_DIR の growth-state-*.json）であり project_dir には
依存しないため、observability contract 互換で引数は受け取るだけ（outcome_metrics と同型）。
"""
from pathlib import Path
from typing import List, Optional


def build_measurement_bug_section(project_dir: Path) -> Optional[List[str]]:
    """複数 PJ で bit-exact 一致した集計値を測定バグ候補として surface する。

    観測可能性:
    - measurement_bug モジュール未解決 → None（沈黙）
    - growth-state cache が無い環境（評価対象が無い）→ None（沈黙）
    - 一致候補が 0 件（健全）→ None（沈黙）
      orphan_store / outcome_metrics と同じ「評価対象が無ければ沈黙」の境界。
      0 / 0.0 / None 同値は detect_measurement_bug で構造的に除外済み（FP 回避・#423）。
    - 候補があれば metric / 一致値 / PJ 群を evidence 付きで出力する。
    """
    try:
        from . import measurement_bug
    except ImportError:
        return None

    metrics_by_pj = measurement_bug.collect_cross_pj_metrics()
    if not metrics_by_pj:
        return None

    alarms = measurement_bug.detect_measurement_bug(metrics_by_pj)
    if not alarms:
        return None

    header = [
        "## Measurement Bug Meta-Check (advisory — スコア重みには未反映)",
        "",
        "複数 PJ で集計値が bit-exact 一致 = 独立走査のはずが揃う測定バグの強シグナル "
        "（#419-#423 を自動化）。0 / 0.0 / None 同値は健全として除外済み。決定論・LLM 非依存。",
        "",
    ]
    body: List[str] = []
    for alarm in sorted(alarms, key=lambda a: (a["metric"], str(a["value"]))):
        metric = alarm["metric"]
        value = alarm["value"]
        projects = alarm["projects"]
        body.append(
            f"  ・⚠ {metric} = {value} が {len(projects)} PJ で完全一致: "
            f"{', '.join(projects)}"
        )
        body.append(
            "      → 集計層のバグ疑い（hardcoded / 共有 state 流用）。"
            "各 PJ の独立性を確認する"
        )
    return header + body + [""]
