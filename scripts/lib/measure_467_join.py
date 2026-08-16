"""#467 §1.5.1 実測（corrections↔usage の join）の純関数群（純関数のみ・store 非依存）。

`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md` §1.5.0 の
「[実測] の再現手順」をコードとして固定する。再現エントリポイントは
`scripts/bench/measure_467_proposal_kinds.py`（本モジュールは純関数のみでテスト専用に
importable。#379 新設凍結の対象外＝store も observability section も作らない）。

tz suffix の混在（`Z` 終端と `+00:00` 終端が同一 instant を指す）があるため、
辞書順比較はしない。必ず ``datetime`` にパースして比較する
（既知 pitfall: ``pitfall_iso8601_lexical_compare_tz_suffix``）。
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """jsonl を全行パースして返す。存在しない・壊れた行は静かに無視する（読み専用・非破壊）。"""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


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


def summarize_corrections(corrections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§1.5.1 の corrections 系集計（総数 / last_skill truthy / source / correction_type 内訳）。"""
    source_counts: Counter = Counter()
    type_counts: Counter = Counter()
    last_skill_truthy = 0
    for c in corrections:
        if c.get("last_skill"):
            last_skill_truthy += 1
        source_counts[c.get("source") or "(missing)"] += 1
        type_counts[c.get("correction_type") or "(missing)"] += 1
    return {
        "total": len(corrections),
        "last_skill_truthy": last_skill_truthy,
        "source_counts": dict(source_counts),
        "correction_type_counts": dict(type_counts),
    }


def is_skill_usage_record(rec: Dict[str, Any]) -> bool:
    """usage.jsonl のレコードが Skill 呼び出し（Agent 呼び出しではない）かを判定する。

    書き手は `hooks/observe.py` の2箇所のみ（`tool_name == "Skill"` と
    `tool_name == "Agent"`）だが、**実 usage.jsonl は単一スキーマではない**
    （2026-08-16 実データ調査で確認: 現行スキーマ以外に旧スキーマ由来のキー集合が
    複数残存し、`outcome` の有無だけでは判別できない。Skill 由来レコードでも
    `ts` でなく `timestamp` を使う旧行が 261 件存在した）。Agent 呼び出しは常に
    `subagent_type` / `agent_id` を持つため、こちらを判別に使うほうが世代を跨いで頑健
    （Skill 呼び出しは `skill_name` を持ち `subagent_type` / `agent_id` を持たない）。
    workflow-conformance 用の別スキーマ（`skill_name` を持たず `skill` を持つ）は
    この条件で自然に除外される。
    """
    return (
        "skill_name" in rec
        and "subagent_type" not in rec
        and "agent_id" not in rec
    )


def count_skill_usage(usage_records: List[Dict[str, Any]]) -> int:
    """usage.jsonl 中の Skill 呼び出し総数（Agent 呼び出しは除く）。"""
    return sum(1 for r in usage_records if is_skill_usage_record(r))


def _skill_usage_timestamp(rec: Dict[str, Any]) -> Any:
    """Skill 呼び出しレコードのタイムスタンプ値を取り出す。

    現行スキーマは `ts` を使うが、旧スキーマの一部行は `timestamp` を使う
    （2026-08-16 実データ調査）。両対応で欠落を防ぐ。
    """
    return rec.get("ts") if "ts" in rec else rec.get("timestamp")


def index_skill_usage_by_session(
    usage_records: List[Dict[str, Any]],
) -> Dict[str, List[Tuple[datetime, str]]]:
    """session_id → [(ts, skill_name), ...]（ts 昇順）の索引を作る。"""
    idx: Dict[str, List[Tuple[datetime, str]]] = defaultdict(list)
    for rec in usage_records:
        if not is_skill_usage_record(rec):
            continue
        dt = parse_iso8601(_skill_usage_timestamp(rec))
        if dt is None:
            continue
        sid = rec.get("session_id") or ""
        if not sid:
            continue
        idx[sid].append((dt, str(rec.get("skill_name") or "")))
    for sid, pairs in idx.items():
        pairs.sort(key=lambda pair: pair[0])
    return idx


def find_preceding_skill(
    correction: Dict[str, Any],
    usage_index: Dict[str, List[Tuple[datetime, str]]],
) -> Optional[str]:
    """correction と同一 session_id で、correction の timestamp より前にある
    最新の Skill 呼び出し名を返す（無ければ None）。

    設計 §1.5.0 の再現手順どおり `datetime` へパースしてから比較する（辞書順比較禁止）。
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


def resolve_skill_md(
    skill_name: Optional[str],
    home_dir: Path,
    project_root: Optional[Path] = None,
) -> Optional[Path]:
    """`discover/runner.py:417-419` と同じ解決規則（global → project の順・bare 名）で
    `SKILL.md` を解決し、見つかった実パスを返す（無ければ None）。

    本番コードの再現（探索対象・順序・マッチ規則を一致させる。2026-08-16 codex cold review
    [Must]1 是正 — 従来は global のみを探索し project 側 `<project>/.claude/skills/...` を
    見ておらず、本番と契約が不一致だった）:

    1. global: ``Path.home().glob(f".claude/skills/{skill_name}/SKILL.md")``
    2. 1 で見つからず `project_root` が指定されていれば project 側を試す:
       ``(project_root / ".claude" / "skills" / skill_name).exists()`` を先にチェックしてから
       同ディレクトリを `SKILL.md` で glob する（`runner.py:418` と同じ existence-guard 付き glob）

    プラグイン namespaced 名（``plugin:skill``）はディレクトリ名に `:` を含むケースが無いため、
    どちらの規則でも原理的に解決しない（§1.5.1 の実測結果と一致する）。

    パスを返す純関数として切り出したのは、探索順（global が project より優先される）を
    テストで固定するため（2026-08-16 codex cold review 4巡目 [Must]1: bool 版だけでは
    global/project 両方に同名 SKILL.md がある場合に探索順を逆転しても ``True`` のまま通ってしまい、
    「global 優先」を検証できていなかった）。
    """
    if not skill_name:
        return None
    global_matches = list(home_dir.glob(f".claude/skills/{skill_name}/SKILL.md"))
    if global_matches:
        return global_matches[0]
    if project_root is None:
        return None
    pj_skill_dir = Path(project_root) / ".claude" / "skills" / skill_name
    if not pj_skill_dir.exists():
        return None
    pj_matches = list(pj_skill_dir.glob("SKILL.md"))
    return pj_matches[0] if pj_matches else None


def skill_md_resolves(
    skill_name: Optional[str],
    home_dir: Path,
    project_root: Optional[Path] = None,
) -> bool:
    """`resolve_skill_md` が何か見つけたかどうかの bool 版（既存呼び出し元向け）。"""
    return resolve_skill_md(skill_name, home_dir, project_root=project_root) is not None
