"""#379/#400 ADR-054 Phase A3（縮小版）: subagent 汚染 corrections の invalidate migration。

ADR-054 §2.2 実測（2026-08-12）: llm_judge channel の weak_signals 336件中33件が
`weak_signal_provenance.source_path` に `/subagents/` を含み（`isSidechain` 未判定による
subagent 出力の誤検出）、うち2件が `promoted=True` で corrections.jsonl まで到達していた。
§414 の決定論基準（source_path に `/subagents/` を含む）を corrections レコードに適用し、
既存の `invalidated` フラグ（安全弁③・ADR-047。`correction_semantic.provenance_weight.is_human_correction` /
`growth_report` が既に除外条件として読む）で論理無効化する。物理削除はしない。

**scope（2026-08-13 頭の判断で拡大）**: 決定論基準は channel を問わず「corrections まで
昇格済み・source_path に /subagents/ を含む」全レコード（llm_judge 2件 + rephrase 6件 = 8件）。
ADR の「残り49件は TTL 自然失効に任せる」の"49件"は**まだ corrections に到達していない
weak_signal** を指しており、既に corrections まで到達した8件は同一の後始末対象（当初の
2件限定は llm_judge の実測記述に引きずられたスコープの取り違え。channel でなく
"corrections 到達済みか" が本来の境界）。

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

from rl_common.correction_id import (  # noqa: E402
    assert_no_unexpected_content_loss,
    atomic_write_text_preserving_mode,
    corrections_write_lock,
    fcntl_unsupported_reason,
    snapshot_identities,
)
from rl_common.persistence import split_corrections_lines  # noqa: E402

_SOURCE_PATH_MARKER = "/subagents/"
_INVALIDATION_REASON = "adr054_a3_subagent_contamination"


def _is_subagent_contaminated_candidate(rec: Dict[str, Any]) -> bool:
    """channel を問わず「corrections 昇格済み・source_path に /subagents/ を含む」を判定する。

    ADR-054 §414 の決定論基準そのもの（channel 限定はしない・2026-08-13 頭の判断で拡大）。
    """
    if rec.get("invalidated"):
        return False  # 既に無効化済み（冪等）
    prov = rec.get("weak_signal_provenance") or {}
    source_path = prov.get("source_path") or ""
    return _SOURCE_PATH_MARKER in source_path


def invalidate_subagent_contaminated_corrections(
    corrections_file: Path, *, dry_run: bool = True
) -> Dict[str, Any]:
    """corrections_file 内の subagent 汚染レコード（channel 不問）を無効化する（既定 dry-run）。

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

    if dry_run:
        return _invalidate_text(
            corrections_file,
            corrections_file.read_text(encoding="utf-8"),
            dry_run=True,
        )[0]
    reason = fcntl_unsupported_reason()
    if reason is not None:
        return {
            "corrections_file": str(corrections_file), "dry_run": False,
            "candidates": [], "invalidated": 0, "error": reason,
        }
    with corrections_write_lock(corrections_file):
        text = corrections_file.read_text(encoding="utf-8")
        report, body, touched = _invalidate_text(corrections_file, text, dry_run=False)
        if touched:
            assert_no_unexpected_content_loss(
                snapshot_identities(text), snapshot_identities(body),
                touched_before=snapshot_identities("\n".join(touched)),
            )
            atomic_write_text_preserving_mode(corrections_file, body)
        return report


def _invalidate_text(corrections_file: Path, text: str, *, dry_run: bool):
    now = datetime.now(timezone.utc).isoformat()
    lines: List[Union[str, Dict[str, Any]]] = []
    candidates: List[str] = []
    touched: List[str] = []

    for raw_line in split_corrections_lines(text):
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

        if _is_subagent_contaminated_candidate(rec):
            candidates.append(str(rec.get("weak_signal_key")))
            touched.append(raw_line)
            if not dry_run:
                rec["invalidated"] = True
                rec["invalidated_at"] = now
                rec["invalidation_reason"] = _INVALIDATION_REASON
                lines.append(rec)
                continue
        lines.append(raw_line)

    body = "".join(
        (json.dumps(line, ensure_ascii=False) if isinstance(line, dict) else line) + "\n"
        for line in lines
    )
    report = {
        "corrections_file": str(corrections_file),
        "dry_run": dry_run,
        "candidates": candidates,
        "invalidated": len(candidates) if not dry_run else 0,
    }
    return report, body, touched


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="ADR-054 A3（縮小版）: subagent 汚染 corrections（channel 不問）を"
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

    report = invalidate_subagent_contaminated_corrections(
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
