"""Advisory Decisions の observability セクション（#284 / #267 Sprint 1）。

advisory detector は長らく「表示するだけ」で、提案が accept されたか却下されたかを
一切追跡していなかった（#267 実測前提2: advisory は decision lane に1件も載っていない）。
#284 で emit→drain に載せた結果を、ここで detector 単位に surface する。

採用率の分母は「判断された数」でなく「提示された数」（surfaced）でなければならない
（#267 実測: freeze 解除条件が「detector 別 surfaced/accepted 集計が3ヶ月分揃ったら」の
ため、surfaced を記録しない限りこの条件は構造的に成立しない）。surfaced/deferred は
``evolve_decisions.ingest_decisions`` が drain 到達時に記録する（#267 Sprint 1）。

「どの detector の提案が実際に採用されているか」が見えて初めて、効かない detector を
淘汰できる（#267 Sprint 3）。書きっぱなしの advisory を1つ減らすための reader でもある。

observability contract 互換 ``(project_dir) -> Optional[List[str]]``。決定論・LLM 非依存。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section


def _compute(project_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        from advisory_decision_log import read_advisory_decisions, summarize_by_detector
        from pj_slug import resolve_pj_slug
    except ImportError:
        return None

    # slug は optimize_history / advisory ストアの書込側と同じ単一ソースで解決する
    # （evolve_decisions.resolve_slug も同関数の thin wrapper・#492）。observability 供給
    # モジュールに ~/.claude 由来の module 定数を持ち込まないため直接 import する。
    records = read_advisory_decisions(slug=resolve_pj_slug(project_dir))
    return {"summary": summarize_by_detector(records), "total": len(records)}


def _render(data: Dict[str, Any]) -> List[str]:
    summary: Dict[str, Dict[str, int]] = data["summary"]
    lines = [
        f"ℹ advisory 提案の記録を {data['total']} 件保持（detector 別）",
        "",
        "| detector | surfaced | accept | reject | deferred | 採用率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for detector_id in sorted(summary):
        counts = summary[detector_id]
        surfaced = counts.get("surfaced", 0)
        accept = counts.get("accept", 0)
        reject = counts.get("reject", 0)
        deferred = counts.get("deferred", 0)
        # 採用率の分母は surfaced（提示された数）。accept/reject 判断された数ではない
        # （#267 実測: 従来は accept+reject を分母にしており、freeze 解除条件の
        # 「surfaced/accepted 集計」が成立していなかった）。
        rate = f"{accept / surfaced:.0%}" if surfaced else "-"
        lines.append(
            f"| {detector_id} | {surfaced} | {accept} | {reject} | {deferred} | {rate} |"
        )

    zero = sorted(d for d, c in summary.items() if c.get("accept", 0) == 0)
    if zero:
        lines += [
            "",
            f"⚠ accept 0 件の detector: {', '.join(zero)}"
            "（提案が採用されていない＝淘汰候補。件数が少ないうちは判断を保留する）",
        ]
    return lines


def build_advisory_decisions_section(project_dir: Path) -> Optional[List[str]]:
    """detector 別の advisory 提案 surfaced/accept/reject/deferred を audit に surface する。

    観測可能性:
    - モジュール未解決 → None（沈黙）
    - 記録 0 件 → None（沈黙）。lane を1度も通していない PJ で「0 件」を出すと
      「評価対象が無い PJ では observability を空にする」既存契約
      （test_empty_when_no_observability_artifacts）を破るため、他の clean-時-沈黙
      section（invalid_frontmatter / subagent_noise）と同じ扱いにする
    - 記録あり → detector 別テーブル（surfaced/accept/reject/deferred/採用率）+
      accept 0 件 detector の ⚠
    """
    return build_advisory_section(
        project_dir,
        title="Advisory Decisions",
        compute=_compute,
        applicable=lambda data: data["total"] > 0,
        render=_render,
    )
