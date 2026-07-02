"""Memory Contagion（評価源バイアス伝播）observability セクション（#73）。

`sections_skill_vuln` / `sections_capture` と同型の builder
（observability contract 互換 `(project_dir) -> Optional[List[str]]`）。当 PJ の評価源を
human / machine に分解し、機械評価源の蓄積偏り（authority bias 増幅）を advisory に surface
する（決定論・LLM 非依存・スコア非関与）。markdown / 構造化 両経路で出るよう builder 1 関数に
集約する（ADR-028）。

surface 規則:
- ContagionReport.applicable=False（評価データ無し）→ None（非該当で沈黙）。
- verdict="healthy"          → ✓ + 内訳（評価源バランス健全）。
- verdict="no_human_baseline" → ℹ + 内訳 + 基準が立たない旨の誘導（⚠ ではない＝cry wolf しない）。
- verdict="contagion_risk"   → ⚠ + 内訳 + evidence（corrections / idioms 別の数）+ 是正の方向。
"""
from pathlib import Path
from typing import List, Optional

from . import memory_contagion
from .advisory import build_advisory_section


def build_memory_contagion_section(project_dir: Optional[Path]) -> Optional[List[str]]:
    """評価源バイアスの記憶伝播を audit に surface する（#73）。"""

    def compute(proj: Path):
        return memory_contagion.compute_contagion(proj)

    def applicable(report) -> bool:
        # 評価データが 1 件も無い PJ はこのチェック非該当 → 沈黙。
        return report.applicable

    def render(report) -> List[str]:
        breakdown = (
            f"human={report.human_total} / machine={report.machine_total}"
            f"（corrections: human {report.human_corrections} / machine {report.machine_corrections} ・ "
            f"idioms: confirmed {report.confirmed_idioms} / unconfirmed {report.unconfirmed_idioms}）"
        )

        if report.verdict == "contagion_risk":
            return [
                "⚠ 機械評価源の蓄積が人間確認源を大きく上回っています（authority bias 増幅の兆候）。"
                "偏った評価源の経験が記憶 / correction に蓄積され、将来の提案・採点に伝染する可能性"
                "（Memory Contagion）。`/evolve-anything:reflect` の昇格や今日の修正確認で人間確認源を"
                "増やし、評価源のバランスを取り戻してください（advisory・スコアには影響しません, #73）。",
                breakdown,
            ]

        if report.verdict == "no_human_baseline":
            return [
                "ℹ 人間確認源がゼロのため評価源バイアスを判定できません（比較基準が無い）。"
                "`/evolve-anything:reflect` / 今日の修正確認（daily-review）で確認を回すと基準が立ち、"
                "以降は偏りを検出できるようになります（advisory, #73）。",
                breakdown,
            ]

        # healthy
        return [
            "✓ no contagion signal（評価源バランス健全）",
            breakdown,
        ]

    return build_advisory_section(
        project_dir,
        title="Memory Contagion（評価源バイアス伝播）",
        compute=compute,
        applicable=applicable,
        render=render,
    )
