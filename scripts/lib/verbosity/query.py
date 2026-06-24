"""verbosity.query — 冗長率 / パターン Top-N の集計（#75）。

read_candidates / read_verdicts（read-only 純度）の結果を集計し、候補数 / 判定済数 /
冗長率 / パターン Top-N を返す。``judged >= min_judged`` の floor ゲートで
サンプル不足のノイズを抑制する（subagent_traces.query の floor 思想に倣う）。
決定論・ゼロ LLM。
"""
from __future__ import annotations

import collections
from pathlib import Path
from typing import Dict, List, Optional

from . import PATTERNS
from . import store as _store

# floor の既定値。判定済み件数がこれ未満なら冗長率を率として出さない（不足を明示）。
DEFAULT_MIN_JUDGED = 3


def verbosity_summary(
    slug: str,
    *,
    min_judged: int = DEFAULT_MIN_JUDGED,
    top_n: int = 5,
    data_dir: Optional[Path] = None,
) -> Dict:
    """当 PJ の冗長性サマリを集計する（floor ゲート付き）。

    Returns:
        {
          "candidates": 候補総数（hash dedup 済）,
          "judged": 判定済み件数,
          "pending": 未判定件数,
          "verbose": 無駄に冗長と判定された件数,
          "verbose_rate": float | None,   # judged < min_judged のとき None（不足）
          "patterns": [{"pattern": str, "count": int, "label": str}, ...],  # Top-N
        }
    """
    candidates = _store.read_candidates(slug, data_dir=data_dir)
    verdicts = _store.read_verdicts(slug, data_dir=data_dir)

    cand_hashes = {c.get("hash") for c in candidates if c.get("hash")}
    judged = len(verdicts)
    pending = len(cand_hashes - set(verdicts.keys()))

    verbose = 0
    pat_counter: collections.Counter = collections.Counter()
    for rec in verdicts.values():
        if rec.get("verbose"):
            verbose += 1
            for p in rec.get("patterns") or []:
                if p in PATTERNS:
                    pat_counter[p] += 1

    if judged >= min_judged:
        verbose_rate = round(verbose / judged, 4) if judged else 0.0
    else:
        verbose_rate = None  # 不足: 率を出さず呼び出し側がデータ不足を明示する。

    patterns: List[Dict] = [
        {"pattern": p, "count": c, "label": PATTERNS[p]}
        for p, c in pat_counter.most_common(top_n)
    ]

    return {
        "candidates": len(candidates),
        "judged": judged,
        "pending": pending,
        "verbose": verbose,
        "verbose_rate": verbose_rate,
        "patterns": patterns,
    }
