"""advisory 提案の accept/reject/surfaced/deferred 記録ストア（#284 / #267 Sprint 1）。

audit の advisory detector は「ファイルが変わったか」で accept を判定する emit→drain lane
（``evolve_decisions``）に1件も載っていなかった。#284 でその配線を通したが、判断の記録先を
既存の ``optimize_history``（fitness_func=skill_quality）と共有してはいけない:

  advisory の対象は pytest.ini・rules・SKILL.md と**異種**であり、skill_quality で採点される
  母集団に混ぜると「混合でなく増量」という不変条件が壊れる。これは
  ``evolve_decisions._extract_candidates`` が remediation の fix を対象外にしているのと同じ理由。

そのため advisory の判断は本モジュールの専用ストアへ分離して記録する。集計は detector 単位で
行い、audit の observability section が読む（write-only ストアを作らない）。

採用率の分母は本来「判断された数」でなく「提示された数」（#267 実測）。この不変条件を保つため
``decision`` は2系統に分かれる:

  - **terminal**（``accept`` / ``reject``）: 同じ提案に対して排他的な最終状態。``recorded_at``
    最新のものだけが勝つ（reject の後に accept すれば accept が残る）
  - **fact**（``surfaced`` / ``deferred``）: 最終状態と独立に記録する事実。同じ提案が
    「surfaced かつ deferred」「surfaced かつ accept」のように複数の fact/terminal を
    **同時に持てる**（deferred のまま後日 accept された場合、両方の記録が残る）

冪等性は read 時 collapse で担保する。terminal 同士は ``(pj_slug, proposal_id)`` 単位で
last-write-wins、fact は ``(pj_slug, proposal_id, decision)`` 単位で last-write-wins（同じ
fact を複数回書いても1件に畳む・件数を水増ししない）。「最後」は **``recorded_at`` で決める**
のであって読み出し順ではない（#290-4）。union read は canonical 先頭で legacy が後に来るため、
単純な後勝ちにすると legacy の**古い** reject が canonical の**新しい** accept を上書きする。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from rl_common.store_write import store_write  # noqa: E402
from store_read_union import iter_read_store_paths, pj_slug_match  # noqa: E402

STORE_NAME = "advisory_decisions.jsonl"

# terminal（排他的な最終状態）と fact（最終状態と独立な事実）で collapse 単位が異なる。
# #267 Sprint 1: surfaced/deferred を追加（旧 DECISIONS=("accept","reject") から拡張）。
_TERMINAL_DECISIONS: FrozenSet[str] = frozenset({"accept", "reject"})
_FACT_DECISIONS: FrozenSet[str] = frozenset({"surfaced", "deferred"})
DECISIONS = tuple(sorted(_TERMINAL_DECISIONS | _FACT_DECISIONS))


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


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _recorded_at(rec: Dict[str, Any]) -> datetime:
    """``recorded_at`` を比較可能な aware datetime にする（欠損・不正は最古扱い）。

    文字列の辞書順比較はしない（``Z`` 終端と ``+00:00`` 終端で同一時刻が不一致になる
    ISO8601 の罠）。naive は UTC とみなす。
    """
    raw = rec.get("recorded_at")
    if not raw:
        return _EPOCH
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _collapse_key(pj_slug: str, proposal_id: str, decision: Any) -> Tuple[str, str, str]:
    """collapse 単位を返す。terminal（accept/reject）は排他的な最終状態として1本に畳み、

    fact（surfaced/deferred）は decision 別に独立して1本に畳む（同じ提案が terminal と
    fact を同時に持てる。deferred のまま後日 accept された場合、両方の記録を残すため）。
    """
    bucket = "terminal" if decision in _TERMINAL_DECISIONS else str(decision)
    return (pj_slug, proposal_id, bucket)


def read_advisory_decisions(slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """当 PJ の記録を read 時 collapse して返す（``_collapse_key`` 単位）。

    read は寛容 union（canonical + legacy + plugins-data）。壊れた行は黙って捨てる
    （観測レーンなので1行の破損で全体を失わせない）。

    勝者は ``recorded_at`` の新しい方（#290-4）。同時刻なら canonical 側（union の先頭
    パス）を優先し、同一ファイル内の同時刻なら後の行（append 順＝時系列）を採る。
    key に ``pj_slug`` を含めるのは ``slug=None``（全 PJ 読み）で別 PJ の同名提案が
    互いを消さないようにするため。
    """
    best: Dict[tuple, tuple] = {}
    for path_rank, path in enumerate(iter_read_store_paths(STORE_NAME)):
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines):
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
            key = _collapse_key(str(rec.get("pj_slug") or ""), str(pid), rec.get("decision"))
            rank = (_recorded_at(rec), -path_rank, line_no)
            if key not in best or rank > best[key][0]:
                best[key] = (rank, rec)
    return [best[key][1] for key in sorted(best)]


def summarize_by_detector(
    records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """detector 別の surfaced / accept / reject / deferred 件数（#267 Sprint 1）。

    採用率の分母（surfaced）を分子（accept）と同じ表で見られるようにする土台。
    """
    summary: Dict[str, Dict[str, int]] = {}
    for rec in records:
        detector_id = str(rec.get("detector_id") or "unknown")
        bucket = summary.setdefault(
            detector_id, {"surfaced": 0, "accept": 0, "reject": 0, "deferred": 0}
        )
        decision = rec.get("decision")
        if decision in bucket:
            bucket[decision] += 1
    return summary
