"""#376 既存 legacy accept レコードの無効化 migration（dry-run 既定）。

`ingest_decisions` が accept 条件を「ディスク差分のみ」から「明示 decision イベント
AND ディスク差分」へ是正した（AC1/AC2）ことに伴い、是正前に記録された accept
レコード（`record_evolve_diff_decision` 経由・`source=evolve_remediation`・
`fitness_func=skill_quality`・`human_accepted=True` だが `decision_source` を持たない
＝旧 hash-proxy 単独判定で記録された可能性がある）を、削除ではなく無効化する。

optimize / evolve-loop 由来の accept（``source`` が ``evolve_remediation`` 以外）は
別の同期的な人間確認フローを経ているため対象外にする — このモジュールが導入する
``decision_source`` フィールド自体が持たない古いレコードでも、ソースが違えば
「hash-proxy 単独判定バグ」の対象ではない。

無効化は既存フィールドを消さず3フィールドを追加するだけ（非破壊）:
  - ``fitness_eligible: false``   — fitness_evolution.run_fitness_evolution の母集団から除外
  - ``invalidated_at``            — 無効化を行った時刻（ISO8601 UTC）
  - ``invalidation_reason``       — "legacy_hash_proxy_false_positive" 固定

冪等: 既に ``fitness_eligible`` を持つレコード（値に関わらず）は候補から除外するため、
二重適用しても再無効化・再書込は起きない。

dry-run が既定（安全側）。実際に書き込むには ``dry_run=False`` を明示する。壊れた
JSON 行は無関係な migration で消さない（生テキストのまま温存する）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from rl_common.file_lock import atomic_write_text  # noqa: E402

# record_evolve_diff_decision（fitness_evolution.py）が付ける固定値。ここだけを対象にし、
# optimize / evolve-loop 由来の accept（同期的な人間確認フローを経ている）を巻き込まない。
_TARGET_SOURCE = "evolve_remediation"
_TARGET_FITNESS_FUNC = "skill_quality"
_INVALIDATION_REASON = "legacy_hash_proxy_false_positive"


def _is_legacy_accept_candidate(rec: Dict[str, Any]) -> bool:
    if rec.get("source") != _TARGET_SOURCE:
        return False
    if rec.get("fitness_func") != _TARGET_FITNESS_FUNC:
        return False
    if rec.get("human_accepted") is not True:
        return False
    if rec.get("decision_source"):
        return False  # #376 是正後の明示 decision イベント由来（対象外）
    if "fitness_eligible" in rec:
        return False  # 既に無効化済み（冪等）
    return True


def invalidate_legacy_accepts(
    history_file: Path, *, dry_run: bool = True
) -> Dict[str, Any]:
    """history_file 内の legacy accept レコードを無効化する（既定 dry-run）。

    Returns:
        {"history_file", "dry_run", "candidates": [id...], "invalidated": int}
    """
    history_file = Path(history_file)
    if not history_file.exists():
        return {
            "history_file": str(history_file),
            "dry_run": dry_run,
            "candidates": [],
            "invalidated": 0,
        }

    now = datetime.now(timezone.utc).isoformat()
    lines: List[Union[str, Dict[str, Any]]] = []
    candidates: List[str] = []

    for raw_line in history_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            lines.append(raw_line)  # 壊れた行は無関係な migration で消さない
            continue
        if not isinstance(rec, dict):
            lines.append(raw_line)
            continue

        if _is_legacy_accept_candidate(rec):
            candidates.append(str(rec.get("id")))
            if not dry_run:
                rec["fitness_eligible"] = False
                rec["invalidated_at"] = now
                rec["invalidation_reason"] = _INVALIDATION_REASON
        lines.append(rec)

    if not dry_run and candidates:
        body = "".join(
            (json.dumps(line, ensure_ascii=False) if isinstance(line, dict) else line) + "\n"
            for line in lines
        )
        atomic_write_text(history_file, body)

    return {
        "history_file": str(history_file),
        "dry_run": dry_run,
        "candidates": candidates,
        "invalidated": len(candidates) if not dry_run else 0,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="#376: legacy accept レコード（hash-proxy 単独判定由来）を"
        "fitness_eligible=False で無効化する（既定 dry-run）"
    )
    parser.add_argument(
        "--history-file", default=None,
        help="対象 optimize_history/<slug>.jsonl の絶対パス（未指定なら --slug から解決）",
    )
    parser.add_argument(
        "--slug", default=None,
        help="optimize_history_store の slug（--history-file 未指定時に使用。"
        "未指定なら現在の cwd から worktree 安全に解決）",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="実際に書き込む（既定は dry-run で候補提示のみ）",
    )
    args = parser.parse_args()

    if args.history_file:
        history_file = Path(args.history_file)
    else:
        import optimize_history_store as ohs

        slug = args.slug or ohs.resolve_slug()
        history_file = ohs.history_path(slug)

    report = invalidate_legacy_accepts(history_file, dry_run=not args.apply)
    mode = "dry-run" if report["dry_run"] else "適用済み"
    print(f"[legacy_accept_migration] {report['history_file']}（{mode}）")
    print(f"  候補: {len(report['candidates'])} 件 {report['candidates']}")
    print(f"  無効化: {report['invalidated']} 件")
    if report["dry_run"] and report["candidates"]:
        print("  --apply を付けて再実行すると書き込まれます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
