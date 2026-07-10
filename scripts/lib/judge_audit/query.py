"""judge_audit.query — false-pass 率の集計（floor ゲート付き・#188）。

read_verdicts（read-only 純度）の結果を集計し、判定済み件数 / false-pass 件数 / 率を返す。
``judged >= min_judged`` の floor ゲートでサンプル不足のノイズを抑制する
（verbosity.query / subagent_traces.query の floor 思想に倣う）。決定論・ゼロ LLM。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from . import store as _store
from .fixtures import FIXTURES

# floor の既定値。判定済み件数がこれ未満なら false-pass 率を率として出さない（不足を明示）。
# verbosity（floor=3）より高い floor=5 を採る: judge のバイアス計測は単発ノイズの影響を
# 受けやすいため保守的に据える（根拠は spec/components.md）。ただし fixture 総数がこれを
# 下回る場合は effective_min_judged() が min(DEFAULT_MIN_JUDGED, total_fixtures()) に
# キャップする（全 fixture 判定済みでも率が永遠に出ない構造的欠陥を防ぐ不変式）。
DEFAULT_MIN_JUDGED = 5

# false-pass 率の危険域判定閾値。The Blind Curator（arXiv 2607.07436）はスキル退役の閾値を
# 鋭く無効化するリスクを指摘するため、一般的な誤検知許容より低い 0.2（5件中1件以上）を
# 保守的な既定値として採る（詳細な根拠は spec/components.md）。
FALSE_PASS_WARN_THRESHOLD = 0.2


def total_fixtures() -> int:
    """登録済み欠陥タスク fixture の総数。"""
    return len(FIXTURES)


def effective_min_judged(min_judged: int = DEFAULT_MIN_JUDGED) -> int:
    """実効 floor（fixture 総数でキャップした min_judged）を返す。

    fixture 総数が DEFAULT_MIN_JUDGED 未満だと「全 fixture を判定しても judged が
    floor に届かず false_pass_rate が構造的に永遠に None」になる（section が永久に
    データ不足表示から抜けられないバグ）。この不変式（全件判定済みなら必ず率が出る）
    を保つため、fixture 総数でキャップする。
    """
    total = total_fixtures()
    if total <= 0:
        return min_judged
    return min(min_judged, total)


def false_pass_summary(
    slug: str,
    *,
    min_judged: int = DEFAULT_MIN_JUDGED,
    data_dir: Optional[Path] = None,
) -> Dict:
    """当 PJ の judge false-pass サマリを集計する（floor ゲート付き）。

    Returns:
        {
          "total_fixtures": 登録済み fixture 総数,
          "judged": 判定済み件数,
          "pending": 未判定件数,
          "false_pass": false-pass と判定された件数,
          "false_pass_rate": float | None,   # judged < effective_min_judged のとき None（不足）
          "effective_min_judged": 実際に適用された floor（fixture 総数でキャップ済み）,
        }
    """
    verdicts = _store.read_verdicts(slug, data_dir=data_dir)
    total = total_fixtures()
    eff_floor = effective_min_judged(min_judged)

    judged = len(verdicts)
    pending = max(0, total - judged)
    false_pass = sum(1 for v in verdicts.values() if v.get("false_pass"))

    if judged >= eff_floor:
        rate = round(false_pass / judged, 4) if judged else 0.0
    else:
        rate = None  # 不足: 率を出さず呼び出し側がデータ不足を明示する。

    return {
        "total_fixtures": total,
        "judged": judged,
        "pending": pending,
        "false_pass": false_pass,
        "false_pass_rate": rate,
        "effective_min_judged": eff_floor,
    }
