"""corrections ↔ usage.jsonl の read-time join（#478）。

`last_skill` の前向き write（`correction_semantic/promote.py`）は一時ファイル方式（TTL 24h）に
依存しており、朝の y/n による採用は検出から数日後に走るため原理的に間に合わない
（`learning_derive_state_from_logs_not_forward_write`: 派生状態は read 時にログから導出する）。

本モジュールは `scripts/bench/measure_467_proposal_kinds.py`（#467）で先に検証された
join ロジックの production 版・単一ソース。bench 側の `measure_467_join.py` はこのモジュールを
re-export し、実装を二重に持たない。

join キーは ``session_id``（スキル名の namespace 差 = `plugin:skill` / bare の混在は
join そのものには影響しない。derived な `last_skill` の値は usage.jsonl の生の
``skill_name`` をそのまま返す。plugin namespaced 値の bare 化は downstream の
SKILL.md 解決側（`discover/runner.py` の `bare_skill_name` 呼び出し）が既に担っており、
ここで正規化すると二重変換になるため行わない）。

tz suffix の混在（``Z`` 終端と ``+00:00`` 終端が同一 instant を指す）があるため、
辞書順比較はしない。必ず ``datetime`` にパースして比較する
（既知 pitfall: ``pitfall_iso8601_lexical_compare_tz_suffix``）。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from rl_common.usage_schema import is_skill_usage_record, usage_timestamp


def parse_iso8601(ts: Any) -> Optional[datetime]:
    """ISO8601 文字列を datetime にパースする。`Z` 終端と `+00:00` 終端を同一視する。

    辞書順比較は tz suffix 混在で崩れるため使わない
    （`pitfall_iso8601_lexical_compare_tz_suffix`）。naive（tz 無し）文字列は UTC とみなす。
    パース不能・空文字は None。
    """
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def index_skill_usage_by_session(
    usage_records: List[Dict[str, Any]],
) -> Dict[str, List[Tuple[datetime, str]]]:
    """session_id → [(ts, skill_name), ...]（ts 昇順）の索引を作る。

    Agent 呼び出し（`is_skill_usage_record` が False を返すレコード）は除外する。
    """
    idx: Dict[str, List[Tuple[datetime, str]]] = defaultdict(list)
    for rec in usage_records:
        if not is_skill_usage_record(rec):
            continue
        dt = parse_iso8601(usage_timestamp(rec))
        if dt is None:
            continue
        sid = rec.get("session_id") or ""
        if not sid:
            continue
        idx[sid].append((dt, str(rec.get("skill_name") or "")))
    for pairs in idx.values():
        pairs.sort(key=lambda pair: pair[0])
    return idx


def find_preceding_skill(
    correction: Dict[str, Any],
    usage_index: Dict[str, List[Tuple[datetime, str]]],
) -> Optional[str]:
    """correction と同一 session_id で、correction の timestamp より前にある
    最新の Skill 呼び出し名を返す（無ければ None）。

    厳密に「前」（``<``）のみを採用する。同時刻（``==``）は除外する。
    """
    sid = correction.get("session_id") or ""
    pairs = usage_index.get(sid)
    if not pairs:
        return None
    corr_dt = parse_iso8601(correction.get("timestamp"))
    if corr_dt is None:
        return None
    latest: Optional[str] = None
    for dt, skill_name in pairs:
        if dt < corr_dt:
            latest = skill_name
        else:
            break
    return latest


def resolve_preceding_skills(
    corrections: List[Dict[str, Any]],
    usage_records: List[Dict[str, Any]],
) -> List[Optional[str]]:
    """corrections の各要素に対応する「直前 Skill 名」（無ければ None）のリスト。"""
    idx = index_skill_usage_by_session(usage_records)
    return [find_preceding_skill(c, idx) for c in corrections]


def attach_last_skill(
    corrections: List[Dict[str, Any]],
    usage_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """corrections の各要素に `last_skill` を read-time join で補完した**コピー**を返す。

    既に `last_skill` が truthy な値を持つ correction（旧 hook writer 由来の 2 件）は
    上書きしない。入力 `corrections` は変更しない（呼び出し側の副作用汚染を避ける）。

    production の全 reader（`discover/runner.py` の instruction_violation 検出・
    `pitfall_manager/detection.py` を呼ぶ `discover/runner.py` の pitfall_candidates 検出）は
    この関数を経由する単一ソース（design-before-fanout: 同型の join を2箇所で独立実装しない）。
    """
    idx = index_skill_usage_by_session(usage_records)
    result: List[Dict[str, Any]] = []
    for c in corrections:
        c2 = dict(c)
        if not c2.get("last_skill"):
            c2["last_skill"] = find_preceding_skill(c, idx)
        result.append(c2)
    return result
