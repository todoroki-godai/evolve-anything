#!/usr/bin/env python3
"""ADR-055 C-0 陽性対照（Positive Control。#534）。

Codex 側較正（``phase1_c0_calibration.py``）の指摘率 0.0%（0/227）が
「真に陰性」（仮説P）か「検査が機能していない」（仮説Q）かを切り分けるため、
CC 側の既知の陽性発話（``correction_idioms.jsonl`` の evolve-anything 分・
実測23件）を**同じ隔離パイプライン**で再判定し、陽性が再現するかを確認する。

**既存コードは一切変更しない**。**本番ストアには一切書き込まない**
（``utterances.db`` は read-only で開く。``read_idioms()``/``run_daily_judge`` の
DI 出力先は全て隔離した一時ディレクトリ）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import phase1_codex_probe as _probe  # noqa: E402 — 本番ストアガードを re-use


def load_known_positives(pj_slug: str = "evolve-anything") -> List[Dict[str, Any]]:
    """correction_idioms.jsonl から既知の陽性（pj_slug一致）を抽出する（read-only）。"""
    from correction_semantic.store import read_idioms

    return [r for r in read_idioms() if r.get("pj_slug") == pj_slug]


def resolve_full_utterances(
    idiom_records: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """idiom の provenance (source_path, line_no) から utterances.db を read-only で引き、
    元の完全な utterance（text/prev_action 含む）を復元する。

    Returns: (utterances, resolved_count, missing_count, prev_action_found_count)
    """
    from utterance_archive import store as ustore
    from utterance_archive.ingest import default_db_path

    db_path = default_db_path()
    resolved: List[Dict[str, Any]] = []
    missing = 0
    prev_action_found = 0

    with ustore.connection(db_path, read_only=True) as con:
        for rec in idiom_records:
            prov = rec.get("provenance") or {}
            source_path = prov.get("source_path", "")
            line_no = prov.get("line_no", "")
            row = None
            if con is not None:
                row = con.execute(
                    "SELECT source_path, line_no, pj_slug, session_id, timestamp, text, "
                    "text_hash, prev_action, source_kind, extractor_version "
                    "FROM utterances WHERE source_path = ? AND line_no = ?",
                    [source_path, line_no],
                ).fetchone()
            if row is not None:
                cols = [
                    "source_path", "line_no", "pj_slug", "session_id", "timestamp",
                    "text", "text_hash", "prev_action", "source_kind", "extractor_version",
                ]
                u = dict(zip(cols, row))
                if u.get("prev_action"):
                    prev_action_found += 1
                resolved.append(u)
            else:
                missing += 1
                # フォールバック: provenance の（200字切詰め済み）情報のみで組み立てる。
                resolved.append(
                    {
                        "source_path": source_path,
                        "line_no": line_no,
                        "pj_slug": "evolve-anything",
                        "session_id": prov.get("session_id", ""),
                        "timestamp": rec.get("detected_at", ""),
                        "text": prov.get("text", ""),
                        "text_hash": "",
                        "prev_action": None,
                        "source_kind": "dialogue",
                        "extractor_version": "positive-control-fallback",
                    }
                )

    return resolved, len(idiom_records) - missing, missing, prev_action_found


def run_positive_control(work_dir: Path, cutoff_days: int) -> Dict[str, Any]:
    from correction_semantic.batch import estimate_tokens
    from correction_semantic.judge_runner import run_daily_judge

    idiom_records = load_known_positives("evolve-anything")
    utterances, resolved_count, missing_count, prev_action_found = resolve_full_utterances(
        idiom_records
    )

    pre_estimate = estimate_tokens(utterances)

    work_dir.mkdir(parents=True, exist_ok=True)
    judged_path = work_dir / "correction_judged.jsonl"
    weak_signals_path = work_dir / "weak_signals.jsonl"
    idioms_path = work_dir / "correction_idioms.jsonl"

    store_paths = _probe.production_store_paths()
    data_dir = _probe.resolve_evolve_anything_data_dir()
    hashes_before = _probe.snapshot_production_hashes(store_paths)
    listing_before = _probe.snapshot_data_dir_listing(data_dir)

    result = run_daily_judge(
        run=True,
        daily_utterance_limit=max(300, len(utterances)),
        daily_token_limit=max(200_000, pre_estimate["est_total_tokens"] + 10_000),
        utterances=utterances,
        tracked_projects=None,
        judge_utterance_max_age_days=cutoff_days,
        judged_path=judged_path,
        weak_signals_path=weak_signals_path,
        idioms_path=idioms_path,
        model="haiku",
    )

    hashes_after = _probe.snapshot_production_hashes(store_paths)
    listing_after = _probe.snapshot_data_dir_listing(data_dir)
    hash_ok, hash_violations = _probe.verify_production_unchanged(hashes_before, hashes_after)
    listing_ok, listing_violations = _probe.verify_data_dir_unchanged(listing_before, listing_after)
    guard_ok = hash_ok and listing_ok
    guard_violations = hash_violations + listing_violations

    denominator = result["corrections"] + result["non_corrections"]
    reproduced_rate = (result["corrections"] / denominator) if denominator else None

    if result["corrections"] >= 12:
        verdict = "合格（仮説P支持: judgeは機能しており、Codex側0%はベースラインとして有効）"
    elif result["corrections"] <= 4:
        verdict = "不合格（仮説Q疑い: Codex側0%はベースラインとして無効・原因切り分けが必要）"
    else:
        verdict = "中間（判定不安定・判断は頭に委ねる）"

    return {
        "adr": "055",
        "phase": "Phase1-C0-PositiveControl",
        "note": "裁定Bにより本結果はGo/No-Goを確定させない。judgeの機能検証のみが目的",
        "execution": {
            "known_positives_extracted": len(idiom_records),
            "resolved_via_utterances_db": resolved_count,
            "missing_from_utterances_db": missing_count,
            "prev_action_found": prev_action_found,
            "prev_action_not_found": len(utterances) - prev_action_found,
            "cutoff_days_used": cutoff_days,
            "cutoff_note": "対照実行のみcutoffを拡大（team-lead指示。期間条件を揃える目的ではなくjudgeの機能確認が目的）",
            "pre_estimate_tokens": pre_estimate["est_total_tokens"],
            "judged_path": str(judged_path),
            "weak_signals_path": str(weak_signals_path),
            "idioms_path": str(idioms_path),
        },
        "measurement": {
            "input_count": len(idiom_records),
            "denominator": denominator,
            "corrections_reproduced": result["corrections"],
            "non_corrections": result["non_corrections"],
            "reproduction_rate": reproduced_rate,
            "omitted_verdicts": result["omitted_verdicts"],
            "parse_failed_batches": result["parse_failed_batches"],
            "skipped_batches": result["skipped_batches"],
            "call_failed": result["call_failed"],
            "excluded_untracked_total": result["excluded_untracked_total"],
            "excluded_before_cutoff_total": result["excluded_before_cutoff_total"],
        },
        "verdict": verdict,
        "production_store_guard": {
            "ok": guard_ok,
            "violations": guard_violations,
            "data_dir_file_count_before": len(listing_before),
            "data_dir_file_count_after": len(listing_after),
        },
        "raw_result": result,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--cutoff-days", type=int, default=3650,
        help="対照実行のみ拡大するage cutoff（既定10年=実質無制限）",
    )
    args = parser.parse_args(argv)

    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="phase1_positive_control_"))
    forbid_reason = _probe.validate_out_dir(work_dir)
    if forbid_reason is not None:
        print(f"[positive_control] FATAL: {forbid_reason}", file=sys.stderr)
        return 2

    report = run_positive_control(work_dir, args.cutoff_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "raw_result"}, ensure_ascii=False, indent=2))
    if not report["production_store_guard"]["ok"]:
        print("[positive_control] FATAL: 本番ストアが変更された", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
