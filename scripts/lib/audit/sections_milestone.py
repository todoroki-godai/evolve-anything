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

    #379 Step 4: growth-journal harness 削除で crystallized_rules の唯一のソースが
    失われたため、Mature Operation への昇格判定は構造的に不可能になった
    （growth_engine.detect_phase_no_crystallization は Mature を返さない）。
    Structured Nurturing 到達時に「requires: crystallized_rules >= 10」等の到達不能な
    条件文言は出さず、判定保留中である旨を明示する。
    """
    from growth_engine import Phase

    lines = ["### Next Milestone"]
    if phase == Phase.MATURE_OPERATION:
        lines.append("最終フェーズに到達しています。")
    elif phase == Phase.STRUCTURED_NURTURING:
        lines.append(
            "現在計測可能な最終フェーズ（Structured Nurturing）に到達しています。"
            "Mature Operation の判定は crystallized_rules 計測の廃止（#379）により保留中です。"
        )
    else:
        next_phases = {
            Phase.BOOTSTRAP: ("Initial Nurturing", "sessions >= 10"),
            Phase.INITIAL_NURTURING: ("Structured Nurturing", "sessions >= 50, corrections >= 10"),
        }
        next_name, next_req = next_phases.get(phase, ("?", "?"))
        lines.append(f"Next phase: **{next_name}** — requires: {next_req}")
    lines.append("")
    return lines


def build_next_milestone_section(proj: Path) -> Optional[List[str]]:
    """次フェーズ到達条件だけを軽量に出す（#52-2・LLM/fitness 非依存）。

    フル growth report を常時 ON にすると別の冗長化になるため、標準 audit では
    Next Milestone の1ブロックのみを出す。phase 解決は:
      1. growth-state cache（既に算出済みなら最安）
      2. cache が無ければ telemetry（sessions/human corrections）から
         detect_phase_no_crystallization（#379 Step 4・fitness/LLM は呼ばない）
    growth_engine 自体が import 不能な環境では None（沈黙）にフォールバックする。
    """
    try:
        from growth_engine import Phase, read_cache
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

    # #398 round2 Must 1: growth-journal harness 削除（#379 Step 4）前に保存された
    # phase=mature_operation の cache は STALENESS_HIDE_DAYS（最大30日）以内なら
    # そのまま読めてしまうが、Mature Operation は crystallized_rules 計測廃止により
    # 本経路では判定しない契約（_next_milestone_lines 参照）に反する。信用せず
    # telemetry から再計算する（detect_phase_no_crystallization は Mature を返さない
    # ため、再計算結果は必ず Structured Nurturing 以下に収まり保留契約と整合する）。
    if phase == Phase.MATURE_OPERATION:
        phase = None

    if phase is None:
        # cache が無い → telemetry から軽算出（fitness/LLM は呼ばない）。
        # #379 Step 4: crystallized_rules は growth-journal harness 削除で恒久喪失した
        # ため、human corrections ベースの縮退判定（growth_report.py #448 と同型）を使う。
        try:
            from growth_engine import detect_phase_no_crystallization
            from telemetry_query import query_corrections, query_sessions
            from correction_semantic.provenance_weight import count_human_corrections
            sessions = query_sessions(project=project_name)
            corrections = query_corrections(project=project_name)
            sessions_count = len(sessions) if sessions else 0
            human_count = count_human_corrections(corrections or [])
        except Exception:
            sessions_count = 0
            human_count = 0
        phase = detect_phase_no_crystallization(sessions_count, human_count)

    return _next_milestone_lines(phase)
