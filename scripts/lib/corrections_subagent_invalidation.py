"""#379/#400 ADR-054 Phase A3（縮小版）: subagent 汚染 corrections の invalidate migration。

ADR-054 §2.2 実測（2026-08-12）: llm_judge channel の weak_signals 336件中33件が
`weak_signal_provenance.source_path` に `/subagents/` を含み（`isSidechain` 未判定による
subagent 出力の誤検出）、うち2件が `promoted=True` で corrections.jsonl まで到達していた。
§414 の決定論基準（source_path に `/subagents/` を含む）を llm_judge channel の corrections
レコードに適用し、既存の `invalidated` フラグ（安全弁③・ADR-047。
`correction_semantic.provenance_weight.is_human_correction` /
`growth_report` が既に除外条件として読む）で論理無効化する。物理削除はしない。

**scope**: llm_judge channel のみが対象（ADR §5/§7.1「corrections 昇格済み2件」の出所）。
rephrase channel の同種汚染は A2（`weak_signals/detectors.py` の委譲プロンプト構造除外統合）
適用後の残存分であり、本 migration の対象外＝TTL 45日の自然失効に委ねる
（A3 は縮小・フルの後始末フェーズは作らない・#379）。

無効化は既存フィールドを消さず3フィールドを更新するだけ（非破壊、legacy_accept_migration.py
と同型）:
  - ``invalidated``          — True（既存フィールド。全 reader がこれで除外判定）
  - ``invalidated_at``       — 無効化を行った時刻（ISO8601 UTC）
  - ``invalidation_reason``  — "adr054_a3_subagent_contamination" 固定

冪等: 既に ``invalidated`` が True のレコードは候補から除外するため、二重適用しても
再無効化・再書込は起きない。

dry-run が既定（安全側）。実際に書き込むには ``dry_run=False`` を明示する
（CLI は ``--apply``）。壊れた JSON 行は無関係な migration で消さない（生テキストのまま温存）。
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

_TARGET_CHANNEL = "llm_judge"
_SOURCE_PATH_MARKER = "/subagents/"
_INVALIDATION_REASON = "adr054_a3_subagent_contamination"


def _is_subagent_llm_judge_candidate(rec: Dict[str, Any]) -> bool:
    if rec.get("invalidated"):
        return False  # 既に無効化済み（冪等）
    if rec.get("weak_signal_channel") != _TARGET_CHANNEL:
        return False
    prov = rec.get("weak_signal_provenance") or {}
    source_path = prov.get("source_path") or ""
    return _SOURCE_PATH_MARKER in source_path


def invalidate_subagent_llm_judge_corrections(
    corrections_file: Path, *, dry_run: bool = True
) -> Dict[str, Any]:
    """corrections_file 内の subagent 汚染 llm_judge レコードを無効化する（既定 dry-run）。

    Returns:
        {"corrections_file", "dry_run", "candidates": [weak_signal_key...], "invalidated": int}
    """
    corrections_file = Path(corrections_file)
    if not corrections_file.exists():
        return {
            "corrections_file": str(corrections_file),
            "dry_run": dry_run,
            "candidates": [],
            "invalidated": 0,
        }

    now = datetime.now(timezone.utc).isoformat()
    lines: List[Union[str, Dict[str, Any]]] = []
    candidates: List[str] = []

    for raw_line in corrections_file.read_text(encoding="utf-8").splitlines():
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

        if _is_subagent_llm_judge_candidate(rec):
            candidates.append(str(rec.get("weak_signal_key")))
            if not dry_run:
                rec["invalidated"] = True
                rec["invalidated_at"] = now
                rec["invalidation_reason"] = _INVALIDATION_REASON
        lines.append(rec)

    if not dry_run and candidates:
        body = "".join(
            (json.dumps(line, ensure_ascii=False) if isinstance(line, dict) else line) + "\n"
            for line in lines
        )
        atomic_write_text(corrections_file, body)

    return {
        "corrections_file": str(corrections_file),
        "dry_run": dry_run,
        "candidates": candidates,
        "invalidated": len(candidates) if not dry_run else 0,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="ADR-054 A3（縮小版）: subagent 汚染 llm_judge corrections を"
        "invalidated=True で無効化する（既定 dry-run）"
    )
    parser.add_argument(
        "--corrections-file", default=None,
        help="対象 corrections.jsonl の絶対パス（未指定なら DATA_DIR 配下のデフォルト）",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="実際に書き込む（既定は dry-run で候補提示のみ）",
    )
    args = parser.parse_args()

    if args.corrections_file:
        corrections_file = Path(args.corrections_file)
    else:
        import rl_common as _rc

        corrections_file = Path(_rc.DATA_DIR) / "corrections.jsonl"

    report = invalidate_subagent_llm_judge_corrections(
        corrections_file, dry_run=not args.apply
    )
    mode = "dry-run" if report["dry_run"] else "適用済み"
    print(f"[corrections_subagent_invalidation] {report['corrections_file']}（{mode}）")
    print(f"  候補: {len(report['candidates'])} 件 {report['candidates']}")
    print(f"  無効化: {report['invalidated']} 件")
    if report["dry_run"] and report["candidates"]:
        print("  --apply を付けて再実行すると書き込まれます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
