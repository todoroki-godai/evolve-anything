"""correction_semantic.provenance_weight — corrections の human-source 重み付け（#431 提案3）。

#431 背景の実測: corrections.jsonl 累計 9 件中、本物の人間修正は 1 件。残り 8 件は
Stop hook の機械生成（source="hook"/"backfill" + correction_type="stop"）。この飢餓で
フェーズ昇格条件（corrections>=10, growth_engine）が永久未達 → 全 PJ が initial_nurturing
に固定される。

対処: フェーズ昇格カウントを **human-source のみ**で駆動する。機械ノイズで状態が
動かないようにする。判定は決定論（source フィールド + correction_type の allowlist）。

source 値の出所（codebase 全走査で確認）:
- "hook"                   : hooks/correction_detect.py（hot hook の語彙検出。機械）
- "backfill"               : scripts/backfill_preceding_tool_calls.py（機械）
- "migrate_learnings_queue": scripts/migrate_reflect_queue.py（機械）
- "reflect_confirmed"      : reflect が weak_signals を人間確認後に corrections へ昇格（人間）

human-source は **明示 allowlist**（HUMAN_SOURCES）でしか成立しない。出所不明・欠落は
保守的に機械扱いする（誤って状態を動かさないため）。correction_type="stop"（Stop hook の
先送り/未充足フィードバック）は source が何であれ機械生成として除外する。
"""
from __future__ import annotations

from typing import Any, Dict, List

# 人間確認済みとみなす source 値（明示 allowlist）。
# - "reflect_confirmed": reflect が weak_signals レーンを人間確認後 corrections へ昇格（#431）。
# - "idiom_dict": human-confirmed idiom に一致した新規 weak_signal の自動昇格（ADR-047）。
#   人間が一度承認した idiom_key の機械的再適用なので human-source とみなす（重み 1.0）。
#   雪崩防止: confirmed=True が立つまで idiom_dict 昇格は一切発生しない（idiom_autopromote が保証）。
HUMAN_SOURCES = frozenset({"reflect_confirmed", "idiom_dict"})

# 機械生成として除外する source 値（観測された全機械 source）。
MACHINE_SOURCES = frozenset({"hook", "backfill", "migrate_learnings_queue"})

# source に関わらず機械生成として除外する correction_type（Stop hook フィードバック）。
_MACHINE_CORRECTION_TYPES = frozenset({"stop"})


def is_human_correction(record: Dict[str, Any]) -> bool:
    """correction 1 件が human-confirmed か（フェーズ昇格カウント対象か）を返す。

    判定（保守的・false positive を避ける）:
    1. invalidated=True なら除外（安全弁③: revoke で巻き戻されたレコード・ADR-047）
    2. correction_type が Stop hook 系なら機械（source 不問）
    3. source が HUMAN_SOURCES の明示メンバーなら人間
    4. それ以外（MACHINE_SOURCES / 欠落 / 未知）は機械
    """
    if record.get("invalidated"):
        return False
    if record.get("correction_type") in _MACHINE_CORRECTION_TYPES:
        return False
    return record.get("source") in HUMAN_SOURCES


def count_human_corrections(records: List[Dict[str, Any]]) -> int:
    """records のうち human-confirmed な correction の件数を返す。"""
    return sum(1 for r in (records or []) if is_human_correction(r))
