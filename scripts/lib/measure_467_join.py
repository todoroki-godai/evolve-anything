"""#467 §1.5.1 実測（corrections↔usage の join）の純関数群（純関数のみ・store 非依存）。

`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md` §1.5.0 の
「[実測] の再現手順」をコードとして固定する。再現エントリポイントは
`scripts/bench/measure_467_proposal_kinds.py`（本モジュールは純関数のみでテスト専用に
importable。#379 新設凍結の対象外＝store も observability section も作らない）。

join 本体（`parse_iso8601` / `index_skill_usage_by_session` / `find_preceding_skill` /
`resolve_preceding_skills`）は #478 で production 側の `correction_skill_join.py` へ
移設し、production の 2 reader（`discover/runner.py` / `pitfall_manager/detection.py` 経由）
と本モジュールが同じ実装を共有する（design-before-fanout: bench 専用ファイルに production が
依存する形は避け、実装を二重に持たない）。本モジュールはそれを re-export するのみ。

tz suffix の混在（`Z` 終端と `+00:00` 終端が同一 instant を指す）があるため、
辞書順比較はしない。必ず ``datetime`` にパースして比較する
（既知 pitfall: ``pitfall_iso8601_lexical_compare_tz_suffix``）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

_lib_dir = Path(__file__).resolve().parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from rl_common.usage_schema import is_skill_usage_record  # noqa: E402
from correction_skill_join import (  # noqa: E402
    find_preceding_skill,
    index_skill_usage_by_session,
    parse_iso8601,
    resolve_preceding_skills,
)


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


# is_skill_usage_record は Skill/Agent 判別の単一ソース（rl_common.usage_schema・#480）を
# そのまま re-export する。判別ロジック自体は production 6 箇所と共有し、ここに独自実装は
# 置かない（design-before-fanout: 同型判別を bench 側で再発明しない）。


def count_skill_usage(usage_records: List[Dict[str, Any]]) -> int:
    """usage.jsonl 中の Skill 呼び出し総数（Agent 呼び出しは除く）。"""
    return sum(1 for r in usage_records if is_skill_usage_record(r))


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
