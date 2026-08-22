#!/usr/bin/env python3
"""ADR-055 C-0 較正（移行前ベースライン取得。#534）。

Phase 1 プローブ（``phase1_codex_probe.py``）が出力した判定入力（259件）に対し、
既存の ``correction_semantic.judge_runner.run_daily_judge`` を **実判定
（``run=True``）** で実行し、Codex ログの指摘率を計測する。

**既存コードは一切変更しない**（既存 judge_runner をそのまま呼ぶだけ）。
**本番ストアには一切書き込まない**: ``judged_path`` / ``weak_signals_path`` /
``idioms_path`` を全て同一の隔離ディレクトリへ向け、実行前後で本番ストア
（``utterances.db`` / ``correction_judged.jsonl`` / ``correction_idioms.jsonl`` /
``weak_signals.jsonl``）の byte hash 不変と、DATA_DIR 配下の新規ファイル非出現を
検査する（Must2 で拡張した検査を ``phase1_codex_probe`` から re-use）。

裁定B（ADR-055）: 本スクリプトの実行結果は「移行前ベースライン」の記録であり、
Go/No-Go を確定させない。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import phase1_codex_probe as _probe  # noqa: E402 — 本番ストアガードを re-use（自作しない）

# 事前承認済みの見積もり（llm-batch-guard）。実消費がこれを大幅に超えたら中断する。
APPROVED_UTTERANCE_COUNT = 259
APPROVED_TOKEN_ESTIMATE = 174_025
TOKEN_OVERRUN_FACTOR = 2.0  # 推定の2倍を超えたら中断（レビュー指示）


def load_utterances(path: Path) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_calibration(utterances_path: Path, work_dir: Path) -> Dict[str, Any]:
    from correction_semantic.batch import estimate_tokens
    from correction_semantic.judge_runner import run_daily_judge
    from weak_signals.store import read_signals

    utterances = load_utterances(utterances_path)

    # llm-batch-guard: 実行前に見積もりを再計算し、承認された規模と乖離していないか確認する。
    pre_estimate = estimate_tokens(utterances)
    if pre_estimate["est_total_tokens"] > APPROVED_TOKEN_ESTIMATE * TOKEN_OVERRUN_FACTOR:
        raise SystemExit(
            f"[c0_calibration] FATAL: 推定トークンが承認規模の{TOKEN_OVERRUN_FACTOR}倍を超過 "
            f"（承認 {APPROVED_TOKEN_ESTIMATE} → 実測見積 {pre_estimate['est_total_tokens']}）。中断します。"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    judged_path = work_dir / "correction_judged.jsonl"
    weak_signals_path = work_dir / "weak_signals.jsonl"
    idioms_path = work_dir / "correction_idioms.jsonl"

    # 本番ストア非汚染ガード（Must2 の実装を re-use）。
    store_paths = _probe.production_store_paths()
    data_dir = _probe.resolve_evolve_anything_data_dir()
    hashes_before = _probe.snapshot_production_hashes(store_paths)
    listing_before = _probe.snapshot_data_dir_listing(data_dir)

    result = run_daily_judge(
        run=True,
        # 259件・9バッチが1回のrunで日次上限に阻まれず全て処理されるよう、
        # 承認された規模に十分な余裕を持たせた上限を明示する（本番既定は200件/150,000トークン）。
        daily_utterance_limit=max(300, len(utterances)),
        daily_token_limit=max(200_000, pre_estimate["est_total_tokens"] + 10_000),
        utterances=utterances,
        tracked_projects=None,  # production 既定（evolve-anything は tracked 済み）
        judge_utterance_max_age_days=90,  # CC 側ベースラインと同条件
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

    positives: List[Dict[str, Any]] = []
    if weak_signals_path.exists():
        for sig in read_signals(weak_signals_path):
            prov = sig.get("provenance") or {}
            positives.append(
                {
                    "text": prov.get("text", ""),  # batch.py が既に先頭200字へ切り詰め済み
                    "reason": prov.get("reason", ""),
                    "prev_action": prov.get("prev_action") or None,
                    "idiom": prov.get("idiom", ""),
                    "category": prov.get("category"),
                    "source_path": prov.get("source_path", ""),
                    "line_no": prov.get("line_no", ""),
                }
            )

    denominator = result["corrections"] + result["non_corrections"]
    hit_rate = (result["corrections"] / denominator) if denominator else None

    return {
        "adr": "055",
        "phase": "Phase1-C0",
        "note": "裁定Bにより本結果は移行前ベースラインの記録であり、Go/No-Goは確定させない",
        "execution": {
            "utterances_input": len(utterances),
            "pre_estimate_tokens": pre_estimate["est_total_tokens"],
            "daily_utterance_limit_used": max(300, len(utterances)),
            "daily_token_limit_used": max(200_000, pre_estimate["est_total_tokens"] + 10_000),
            "model": "haiku",
            "run": True,
            "judged_path": str(judged_path),
            "weak_signals_path": str(weak_signals_path),
            "idioms_path": str(idioms_path),
        },
        "denominator_semantics": {
            "input_utterances": len(utterances),
            "judged_denominator": denominator,
            "excluded_from_denominator": {
                "omitted_verdicts": result["omitted_verdicts"],
                "parse_failed_batches": result["parse_failed_batches"],
                "skipped_batches": result["skipped_batches"],
                "assistant_only_skipped": result["assistant_only_skipped"],
                "call_failed_batches": result["call_failed"],
                "excluded_untracked_total": result["excluded_untracked_total"],
                "excluded_before_cutoff_total": result["excluded_before_cutoff_total"],
            },
        },
        "measurement": {
            "denominator": denominator,
            "corrections": result["corrections"],
            "non_corrections": result["non_corrections"],
            "hit_rate": hit_rate,
            "omitted_verdicts": result["omitted_verdicts"],
            "parse_failed_batches": result["parse_failed_batches"],
            "skipped_batches": result["skipped_batches"],
            "call_failed": result["call_failed"],
            "requested_batches": result["requested"],
            "responded_batches": result["responded"],
            "reserved_batches": result["reserved_batches"],
        },
        "positives": positives,
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
    parser.add_argument("--utterances", type=Path, required=True, help="Phase1が出力したutterances.json")
    parser.add_argument("--work-dir", type=Path, default=None, help="隔離作業ディレクトリ（既定: tempfile）")
    parser.add_argument("--out", type=Path, required=True, help="結果JSONの出力先")
    args = parser.parse_args(argv)

    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="phase1_c0_calibration_"))
    forbid_reason = _probe.validate_out_dir(work_dir)
    if forbid_reason is not None:
        print(f"[c0_calibration] FATAL: {forbid_reason}", file=sys.stderr)
        return 2

    report = run_calibration(args.utterances, work_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "raw_result"}, ensure_ascii=False, indent=2))
    if not report["production_store_guard"]["ok"]:
        print("[c0_calibration] FATAL: 本番ストアが変更された", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
