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

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from growth_engine import (
    STRUCTURED_CORRECTIONS_TARGET,
    Phase,
    PHASE_DISPLAY_NAMES,
)
from correction_semantic.provenance_weight import count_human_corrections


def _is_today(ts: Any) -> bool:
    """correction の timestamp が「今日（UTC）」かを判定する（決定論・LLM 非依存）。

    ISO8601 文字列を tolerant にパースする。パース不能 / 欠落は今日でない扱い（保守的）。
    """
    if not isinstance(ts, str) or not ts:
        return False
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # 日付のみ（YYYY-MM-DD）等の簡易フォーマットを救済（先頭の日付部分のみ抽出）
        date_part = raw.split("T", 1)[0].split(" ", 1)[0]
        try:
            dt = datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def count_promoted_today(corrections: Optional[List[Dict[str, Any]]]) -> Dict[str, int]:
    """corrections ストアから「今日昇格した weak_signal 由来 correction」を決定論で数える（#494）。

    実 promote は Step 6.2 の `evolve-reflect --promote-weak`（人間確認）/ idiom_autopromote（自動）が
    corrections.jsonl に書く永続記録なので、build_review の返り値（promoted キー無し）に依存せず
    ここを単一の真実とする。これにより growth_report.promoted_today の「構造的常時0」を根治する。

    判定（weak_signal 由来の昇格のみ・手書き correction や Stop hook 機械生成は除外）:
      - weak_signal_key を持つ（= weak_signals レーンからの昇格）
      - invalidated でない（安全弁③ revoke 済みは除外）
      - timestamp が今日（UTC）
      - promoted_by=="idiom_dict" → autopromoted_today
      - source=="reflect_confirmed"（idiom_dict でない）→ promoted_today

    Returns: {"promoted_today": int, "autopromoted_today": int}
    """
    promoted = 0
    autopromoted = 0
    for rec in corrections or []:
        if rec.get("invalidated"):
            continue
        if not rec.get("weak_signal_key"):
            continue
        if not _is_today(rec.get("timestamp")):
            continue
        if rec.get("promoted_by") == "idiom_dict":
            autopromoted += 1
        elif rec.get("source") == "reflect_confirmed":
            promoted += 1
    return {"promoted_today": promoted, "autopromoted_today": autopromoted}


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

    # ── 今日の昇格件数（#494 発見2: 構造的常時0 の根治）──────────────
    # build_review の返り値（daily）には promoted キーが存在せず、実 promote は Step 6.2 の
    # evolve-reflect --promote-weak が corrections.jsonl に書く。そこで corrections ストアの
    # 「今日の weak_signal 由来昇格」を単一の真実として数える（count_promoted_today）。
    # 後方互換: 明示渡しの live カウント（review_result.daily.promoted / autopromote_result.
    # promoted）が store より多ければ max で勝たせる（同 run の即時表示用）。store 由来導出は
    # 下限保証として常時0を解消する。dict.get None pitfall: (d.get(k) or {}) 形式。
    _today = count_promoted_today(_corrections)

    # #525-1: 「このrun」（明示渡しの live カウント）と「本日累計」（store 由来）を区別する。
    # promoted_today（本日累計）は store の今日の昇格を数えるため、同日に走った別セッション分も
    # 含む。これを「今日の確認で N件昇格」と単独表示すると、当 run で何も承認していなくても
    # 過去の昇格があたかも当 run の成果に見える（#525-1 の混同）。出所を line で明示するため、
    # *_this_run（明示渡し）を別途保持する。
    _review = review_result or {}
    _daily = (_review.get("daily") or {})
    promoted_this_run: int = int((_daily.get("promoted") or 0))
    promoted_today: int = max(promoted_this_run, _today["promoted_today"])

    _autopromote = autopromote_result or {}
    autopromoted_this_run: int = int((_autopromote.get("promoted") or 0))
    autopromoted_today: int = max(autopromoted_this_run, _today["autopromoted_today"])

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
    # #51: 「あと N 件」の分子が何を数えた数かを行内で明示する。is_human_correction
    # （provenance_weight）が human-source とみなすのは reflect 承認（source=reflect_confirmed）と
    # idiom_dict 自動昇格のみで、hot hook の語彙検出（source=hook）・backfill・Stop hook 由来
    # （correction_type=stop）は除外される。ユーザーが「自分のどの操作がここに効くか」を読めるようにする。
    lines.append(
        "  └ カウントされるアクション: /reflect で approve または --promote-weak で昇格した修正"
        "（自動検出・Stop hook 由来は除外）"
    )

    # 今日の昇格行（本日累計が 1 件以上の場合のみ）。
    # #525-1: 「本日累計 N 件昇格（このrunでは M 件）」と出所を明示する。本日累計は store の
    # 今日の weak_signal 由来昇格（同日の別セッション分を含む）、このrun は当 evolve run で明示
    # 渡しされた live カウント。両者を区別しないと過去の昇格が当 run の成果に誤読される。
    total_today = promoted_today + autopromoted_today
    this_run_total = promoted_this_run + autopromoted_this_run
    if total_today > 0:
        parts = []
        if promoted_today > 0:
            parts.append(f"reflect 確認 {promoted_today}件")
        if autopromoted_today > 0:
            parts.append(f"idiom {autopromoted_today}件")
        lines.append(
            f"本日累計 {' / '.join(parts)} が自動化対象に昇格"
            f"（このrunでは {this_run_total} 件）"
        )

    return {
        "phase": phase.value,
        "phase_ja": phase_ja,
        "corrections_human": human_count,
        "corrections_target": target,
        "remaining_to_next": remaining,
        "promoted_today": promoted_today,
        "autopromoted_today": autopromoted_today,
        "promoted_this_run": promoted_this_run,
        "autopromoted_this_run": autopromoted_this_run,
        "lines": lines,
    }
