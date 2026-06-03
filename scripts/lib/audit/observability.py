"""Observability contract — audit が「必ず surface すべき行」を構造化して返す。

背景: #272 で audit の Unmanaged Pitfalls は「該当なしでも ✓ 1行」を出すようにしたが、
evolve は audit の 217KB markdown を phases.audit.report に丸ごと格納するだけで、
assistant が名前付きフェーズを選択読みする運用のため、markdown 中盤に埋もれた observability 行は
surface されなかった（silence != evaluated 原則が観測性 fix 自身の配線で再発したケース）。

対策: observability セクションを生成する builder を _OBSERVABILITY_BUILDERS に一元登録し、
markdown 経路（report.py）と構造化経路（collect_observability → evolve が必ず出力）の双方が
同じリストを単一ソースとして消費する。将来 observability 項目を足しても両経路に自動伝播する。

各 builder の契約: その PJ に非該当（CONTEXT.md / pitfalls.md が無い等）のときだけ None を返し、
該当する場合は clean でも「評価したが該当なし ✓」を含む行リストを必ず返す。
"""
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .sections import (
    build_belief_blocks_section,
    build_calibration_drift_section,
    build_glossary_drift_section,
    build_negative_transfer_section,
    build_unmanaged_pitfalls_section,
)
from .sections_eval import build_eval_saturation_section

# (key, builder) — observability の単一ソース。
# report.py(markdown) と collect_observability(構造化) の両方がこれを消費する。
# 新しい observability セクションはここに 1 行足すだけで両経路に伝播する。
_OBSERVABILITY_BUILDERS: List[Tuple[str, Callable[[Path], Optional[List[str]]]]] = [
    ("glossary_drift", build_glossary_drift_section),
    ("unmanaged_pitfalls", build_unmanaged_pitfalls_section),
    ("belief_blocks", build_belief_blocks_section),
    ("calibration_drift", build_calibration_drift_section),
    ("eval_saturation", build_eval_saturation_section),
    ("negative_transfer", build_negative_transfer_section),
]


def collect_observability(project_dir: Path) -> Dict[str, List[str]]:
    """PJ に該当する observability セクションを key→行リストの dict で返す。

    builder が None を返す項目（その PJ に非該当）は除外する。
    evolve はこの dict を result["observability"] に格納し、必ずサマリに surface する。
    """
    proj = Path(project_dir)
    result: Dict[str, List[str]] = {}
    for key, builder in _OBSERVABILITY_BUILDERS:
        section = builder(proj)
        if section:
            result[key] = section
    return result
