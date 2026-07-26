"""advisory 提案の accept/reject 記録ストア（#284 / #267 Sprint 1）。

audit の advisory detector は「ファイルが変わったか」で accept を判定する emit→drain lane
（``evolve_decisions``）に1件も載っていなかった。#284 でその配線を通したが、判断の記録先を
既存の ``optimize_history``（fitness_func=skill_quality）と共有してはいけない:

  advisory の対象は pytest.ini・rules・SKILL.md と**異種**であり、skill_quality で採点される
  母集団に混ぜると「混合でなく増量」という不変条件が壊れる。これは
  ``evolve_decisions._extract_candidates`` が remediation の fix を対象外にしているのと同じ理由。

そのため advisory の判断は本モジュールの専用ストアへ分離して記録する。集計は detector 単位で
行い、audit の observability section が読む（write-only ストアを作らない）。

冪等性は read 時 collapse で担保する（``(pj_slug, proposal_id)`` の last-write-wins）。
同じ提案を複数回 drain しても最後の判断だけが残る。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from rl_common.store_write import store_write  # noqa: E402
from store_read_union import iter_read_store_paths, pj_slug_match  # noqa: E402

STORE_NAME = "advisory_decisions.jsonl"

# 記録対象の判断種別（skip は記録しない＝未判断は deferred として marker に残る）。
DECISIONS = ("accept", "reject")


def record_advisory_decision(
    *,
    slug: str,
    proposal_id: str,
    detector_id: str,
    target_path: str,
    decision: str,
    run_id: Optional[str] = None,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """advisory 提案1件の判断を append する（write barrier 経由・ADR-049）。"""
    if decision not in DECISIONS:
        raise ValueError(f"unknown advisory decision: {decision}")
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    store_write(
        STORE_NAME,
        {
            "pj_slug": slug,
            "proposal_id": proposal_id,
            "detector_id": detector_id,
            "target_path": target_path,
            "decision": decision,
            "run_id": run_id,
            "reason": reason,
            "recorded_at": stamp,
        },
    )


def read_advisory_decisions(slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """当 PJ の判断を read 時 collapse して返す（``proposal_id`` 単位 last-write-wins）。

    read は寛容 union（canonical + legacy + plugins-data）。壊れた行は黙って捨てる
    （観測レーンなので1行の破損で全体を失わせない）。
    """
    collapsed: Dict[str, Dict[str, Any]] = {}
    for path in iter_read_store_paths(STORE_NAME):
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if slug is not None and not pj_slug_match(rec.get("pj_slug"), slug):
                continue
            pid = rec.get("proposal_id")
            if not pid:
                continue
            collapsed[str(pid)] = rec
    return [collapsed[key] for key in sorted(collapsed)]


def summarize_by_detector(
    records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """detector 別の accept / reject 件数（#267 Sprint 1 の detector 別集計の土台）。"""
    summary: Dict[str, Dict[str, int]] = {}
    for rec in records:
        detector_id = str(rec.get("detector_id") or "unknown")
        bucket = summary.setdefault(detector_id, {"accept": 0, "reject": 0})
        decision = rec.get("decision")
        if decision in bucket:
            bucket[decision] += 1
    return summary
