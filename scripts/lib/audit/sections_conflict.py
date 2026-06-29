"""Memory Conflict（非両立記憶ペア）observability セクション（#83）。

``sections_contagion`` / ``sections_memory`` と同型の builder
（observability contract 互換 ``(project_dir) -> Optional[List[str]]``）。当 PJ の active
memory fact から「同一 specific key を肯定 / 否定で言及する非両立ペア」を決定論で検出し
advisory に surface する（決定論・LLM 非依存・スコア非関与）。markdown / 構造化 両経路で
出るよう builder 1 関数に集約する（ADR-028）。

surface 規則（silence != evaluated）:
- applicable=False（active fact < FLOOR / memory 無し）→ None（非該当で沈黙）。
- conflicts 空 → ✓ no conflicts (N facts scanned)（評価したが該当なし）。
- conflicts あり → ⚠ + 各ペアの evidence（肯定 / 否定 2 fact のパス・対立値）。
"""
from pathlib import Path
from typing import List, Optional

from . import memory_conflict


def build_memory_conflict_section(project_dir: Optional[Path]) -> Optional[List[str]]:
    """非両立な記憶ペアを audit に surface する（#83）。"""
    report = memory_conflict.compute_conflicts(Path(project_dir))

    # active fact が FLOOR 未満 / memory 無し → このチェック非該当 → 沈黙。
    if not report.applicable:
        return None

    header = ["## Memory Conflict（非両立記憶ペアの検出）", ""]

    if not report.conflicts:
        return header + [
            f"✓ no conflicts ({report.total_facts} facts scanned)",
            "",
        ]

    lines = header + [
        f"⚠ 同一 PJ 内に非両立な記憶ペアを {len(report.conflicts)} 件検出しました"
        f"（active memory {report.total_facts} 件中）。同じ対象に対し肯定 / 否定が並存しており、"
        f"`bin/evolve-fleet recall` が矛盾する2記憶を両方掴むと提案が汚染されます。どちらかを "
        f"supersede（時間降格）/ 統合してください（advisory・スコアには影響しません, #83）。",
        "",
    ]
    for c in report.conflicts:
        lines.append(f"- 対象 `{c.key}`:")
        lines.append(f"    肯定: {c.pos_path.name} — {c.pos_value}")
        lines.append(f"    否定: {c.neg_path.name} — {c.neg_value}")
    lines.append("")
    return lines
