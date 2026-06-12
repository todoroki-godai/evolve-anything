#!/usr/bin/env python3
"""growth_report — 成長状態の決定論レポート生成（#448）。

evolve レポート末尾に:
- 「corrections 7/10 — あと3件で構造化育成へ」
- 「今日の確認で idiom N件が自動化対象に昇格」
を表示するための決定論関数。LLM 非依存、ファイル書き込みなし（read-only）。

閾値は growth_engine の定数を import して使う（リテラル直書き禁止）。
閾値変更時は growth_engine.py だけ更新すれば detect_phase / compute_phase_progress /
このレポートが同時に追従する。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from growth_engine import (
    STRUCTURED_CORRECTIONS_TARGET,
    Phase,
    PHASE_DISPLAY_NAMES,
)
from correction_semantic.provenance_weight import count_human_corrections


def build_growth_report(
    project_name: str,
    *,
    corrections: Optional[List[Dict[str, Any]]],
    review_result: Optional[Dict[str, Any]],
    autopromote_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """成長レポート行（決定論）を返す。ファイルには書かない。

    Args:
        project_name: PJ 名（表示用）
        corrections: query_corrections の戻り（human-source カウントに使う）
        review_result: result["correction_review"]（#446 が emit する daily キー配下）
        autopromote_result: result["idiom_autopromote"]（#447 が emit する promoted 件数）

    Returns:
        {
            "phase": str,              # 現在フェーズ（growth_engine.Phase.value）
            "phase_ja": str,           # 日本語フェーズ名
            "corrections_human": int,  # count_human_corrections の結果
            "corrections_target": int, # 次フェーズ閾値（initial→structured は 10）
            "remaining_to_next": int,  # max(0, target - human)
            "promoted_today": int,     # 今 run で reflect_confirmed 昇格した件数
            "autopromoted_today": int, # 今 run で idiom_dict 昇格した件数
            "lines": [str, ...],       # 表示行リスト
        }
    """
    # ── corrections カウント ──────────────────────────────────────
    _corrections = corrections or []
    human_count = count_human_corrections(_corrections)
    target = STRUCTURED_CORRECTIONS_TARGET
    remaining = max(0, target - human_count)

    # ── 今日の昇格件数（dict.get None pitfall: (d.get(k) or {}) 形式）──
    _review = review_result or {}
    _daily = (_review.get("daily") or {})
    promoted_today: int = int((_daily.get("promoted") or 0))

    _autopromote = autopromote_result or {}
    autopromoted_today: int = int((_autopromote.get("promoted") or 0))

    # ── フェーズ表示名（corrections カウントベースで暫定判定）────────
    # ここでは full detect_phase は呼ばず、corrections カウントのみで
    # initial_nurturing を想定（corrections 段階のレポートが目的）。
    # フェーズ判定の権威は audit orchestrator（growth_engine.detect_phase）。
    if human_count >= target:
        phase = Phase.STRUCTURED_NURTURING
    else:
        phase = Phase.INITIAL_NURTURING
    phase_ja = PHASE_DISPLAY_NAMES[phase]["ja"]

    # ── lines 構築 ────────────────────────────────────────────────
    lines: List[str] = []

    # corrections 進捗行
    # #476-4: corrections_human が「何を数えた数か」を明示する。これは prune の
    # 「corrections kept」（全 correction を数える）とは別物で、human-confirmed（reflect 承認 /
    # idiom_dict 自動昇格・機械生成 hook/backfill は除外）のみを数えたフェーズ昇格カウントである。
    if remaining > 0:
        lines.append(
            f"corrections（human-confirmed のみ）{human_count}/{target} — "
            f"あと{remaining}件で{PHASE_DISPLAY_NAMES[Phase.STRUCTURED_NURTURING]['ja']}へ"
        )
    else:
        lines.append(
            f"corrections（human-confirmed のみ）{human_count}/{target} — "
            f"達成・次フェーズ条件は sessions/coherence"
        )

    # 今日の昇格行（どちらかが 1 件以上の場合のみ）
    total_today = promoted_today + autopromoted_today
    if total_today > 0:
        parts = []
        if promoted_today > 0:
            parts.append(f"reflect 確認 {promoted_today}件")
        if autopromoted_today > 0:
            parts.append(f"idiom {autopromoted_today}件")
        lines.append(
            f"今日の確認で {' / '.join(parts)} が自動化対象に昇格"
        )

    return {
        "phase": phase.value,
        "phase_ja": phase_ja,
        "corrections_human": human_count,
        "corrections_target": target,
        "remaining_to_next": remaining,
        "promoted_today": promoted_today,
        "autopromoted_today": autopromoted_today,
        "lines": lines,
    }
