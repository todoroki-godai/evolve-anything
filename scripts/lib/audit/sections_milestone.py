"""Next Milestone（次フェーズ到達条件）の軽量セクション生成（#52-2・決定論・LLM 非依存）。

フル growth report（重い環境 fitness 計算込み）を常時 ON にすると冗長化するため、標準 audit
では Next Milestone の1ブロックだけを軽量サブセットとして常時出す。phase 解決は growth-state
cache 優先・無ければ telemetry から軽算出（fitness/LLM は呼ばない）。

orchestrator.py から切り出した（file-size-budget — orchestrator が 500 行閾値を跨ぐのを防ぐ）。
`_next_milestone_lines` はフル growth report（orchestrator._build_growth_report）とも文言を共有する。
"""
from pathlib import Path
from typing import List, Optional


def _next_milestone_lines(phase) -> List[str]:
    """現フェーズから「次フェーズ到達条件」の行を生成する（#52-2）。

    フル growth report（重い fitness 計算込み）と軽量 milestone（標準 audit 用）の
    どちらからも同じ文言を出す。phase は growth_engine.Phase。
    """
    from growth_engine import Phase

    lines = ["### Next Milestone"]
    if phase == Phase.MATURE_OPERATION:
        lines.append("最終フェーズに到達しています。")
    else:
        next_phases = {
            Phase.BOOTSTRAP: ("Initial Nurturing", "sessions >= 10"),
            Phase.INITIAL_NURTURING: ("Structured Nurturing", "sessions >= 50, corrections >= 10, crystallized_rules >= 3"),
            Phase.STRUCTURED_NURTURING: ("Mature Operation", "sessions > 200, crystallized_rules >= 10, coherence >= 0.7"),
        }
        next_name, next_req = next_phases.get(phase, ("?", "?"))
        lines.append(f"Next phase: **{next_name}** — requires: {next_req}")
    lines.append("")
    return lines


def _count_crystallized_safe(project_name: str) -> int:
    """crystallized rule 件数を例外安全に数える（telemetry 未蓄積でも 0 を返す）。"""
    try:
        from growth_journal import count_crystallized_rules
        return count_crystallized_rules(project=project_name)
    except Exception:
        return 0


def build_next_milestone_section(proj: Path) -> Optional[List[str]]:
    """次フェーズ到達条件だけを軽量に出す（#52-2・LLM/fitness 非依存）。

    フル growth report を常時 ON にすると別の冗長化になるため、標準 audit では
    Next Milestone の1ブロックのみを出す。phase 解決は:
      1. growth-state cache（既に算出済みなら最安）
      2. cache が無ければ telemetry（sessions/corrections/crystallized）から detect_phase
         （coherence は軽量化のため 0.0 固定 = fitness/LLM を呼ばない）
    growth_engine 自体が import 不能な環境では None（沈黙）にフォールバックする。
    """
    try:
        from growth_engine import Phase, detect_phase, read_cache
    except Exception:
        return None

    project_name = proj.resolve().name

    phase: Optional[Phase] = None
    cache = read_cache(project_name)
    if cache is not None:
        phase_val = cache.get("phase")
        if phase_val:
            try:
                phase = Phase(phase_val)
            except ValueError:
                phase = None

    if phase is None:
        # cache が無い → telemetry から軽算出（fitness/LLM は呼ばない）
        try:
            from telemetry_query import query_corrections, query_sessions
            sessions = query_sessions(project=project_name)
            corrections = query_corrections(project=project_name)
            sessions_count = len(sessions) if sessions else 0
            corrections_count = len(corrections) if corrections else 0
        except Exception:
            sessions_count = 0
            corrections_count = 0
        crystallized = _count_crystallized_safe(project_name)
        # coherence は軽量化のため 0.0 固定（fitness 計算を回避）。Mature 判定には
        # coherence>=0.7 が必要なので、軽量経路では Mature には昇格しない（控えめ側）。
        phase = detect_phase(sessions_count, corrections_count, crystallized, 0.0)

    return _next_milestone_lines(phase)
