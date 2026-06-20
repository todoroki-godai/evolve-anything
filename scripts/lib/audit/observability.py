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
from .sections_agent import build_agent_team_section
from .sections_capture import build_capture_rate_section
from .sections_eval import build_eval_saturation_section
from .sections_fanout import build_fanout_cost_section
from .sections_hook import build_hook_drift_section
from .sections_measurement import build_measurement_bug_section
from .sections_memory import build_memory_capability_section
from .sections_multiview import build_multiview_eval_section
from .sections_orphan import build_orphan_store_section, build_store_contract_section
from .sections_outcome import build_outcome_metrics_section
from .sections_paired import build_paired_trajectory_section
from .sections_promotion_readiness import build_promotion_readiness_section
from .sections_testpaths import build_testpaths_coverage_section
from .sections_triage import build_skill_triage_section
from .sections_weak_signals import build_weak_signals_section

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
    ("hook_drift", build_hook_drift_section),
    ("agent_team", build_agent_team_section),
    ("correction_capture", build_capture_rate_section),
    ("orphan_store", build_orphan_store_section),
    ("store_contract", build_store_contract_section),
    ("outcome_metrics", build_outcome_metrics_section),
    ("fanout_cost", build_fanout_cost_section),
    ("memory_capability", build_memory_capability_section),
    ("multiview_eval", build_multiview_eval_section),
    ("paired_trajectory", build_paired_trajectory_section),
    ("measurement_bug", build_measurement_bug_section),
    ("promotion_readiness", build_promotion_readiness_section),
    ("weak_signals", build_weak_signals_section),
    ("testpaths_coverage", build_testpaths_coverage_section),
    ("skill_triage", build_skill_triage_section),
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
