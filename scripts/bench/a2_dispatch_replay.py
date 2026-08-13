#!/usr/bin/env python3
"""A2 (weak_signals _DISPATCH_MARKERS 廃止→is_machinery_prompt 委譲) — 実コーパスリプレイ。

ADR-054 §6「実データ検証の挿入点」①: 「A2 後＝同じ 12 group サンプルを再抽出し
『委譲プロンプト0件』を実測」への対応。ADR §2.3(b) の「今朝の 12 group」は固定の
再現可能スクリプトではなく、当時の実 daily_review.build_review() 出力（production
の read-only 集計関数）をその場で観察した数値だった（本ファイルの調査で確認・
scripts/bench/ にも docs/decisions/drafts/ にも先行スクリプトは存在しなかった）。

本スクリプトは同じ方法論を再現する: production の ``detect_rephrase``
（A2 適用後・is_machinery_prompt + is_dispatch_template_marker へ委譲済み）を
実 utterances.db（全 PJ 横断・直近ウィンドウ）にそのまま適用し、検出された
rephrase シグナルのうち「委譲プロンプト（オーケストレーターが worker/teammate へ
送った dispatch テンプレ）」に該当する件数を数える。

read-only 保証: utterances.db は ``duckdb.connect(..., read_only=True)`` 経由
（query_utterances_all_projects 内部）でのみ開く。本スクリプトは utterances.db /
weak_signals.jsonl のいずれにも書き込まない（--out 指定時のみ repo 内 harness
専用ファイルへ出力）。

使い方:
    python3 scripts/bench/a2_dispatch_replay.py --since-days 30 \
        --out scripts/bench/a2_dispatch_replay_result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent.parent
LIB = REPO / "scripts" / "lib"
sys.path.insert(0, str(LIB))

from utterance_archive.query import query_utterances_all_projects  # noqa: E402
from weak_signals.detectors import detect_rephrase  # noqa: E402

# 評価専用の独立参照セット（production ロジックの複製ではない）。旧 _DISPATCH_MARKERS
# （A2 で削除済み）+ 明らかに dispatch テンプレと分かる補助シグナルを合わせた「目視補助」
# の広いネット。ここでヒットしても production の判定には一切使わない（あくまで検出漏れの
# 見落とし防止フィルタ。最終判断はダンプした本文の目視で行う）。
_REFERENCE_DISPATCH_HINTS = (
    "<task-notification>", "<tool-use-id>", "<summary>", "作業ディレクトリ",
    "あなたは", "エージェントです", "比較実験パターン", "experiment ",
    "<teammate-message", "idle_notification", "another claude session sent a message",
    "===", "worktree", "委譲",
)


def reference_dispatch_suspect(text: str) -> bool:
    low = text.lower()
    return any(h.lower() in low for h in _REFERENCE_DISPATCH_HINTS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since-days", type=int, default=30)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    since = (datetime.now(timezone.utc) - timedelta(days=args.since_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    print(f"[a2-replay] querying utterances.db (all projects, dialogue, since={since})", flush=True)
    rows = query_utterances_all_projects(since=since, source_kinds=("dialogue",))
    print(f"[a2-replay] loaded {len(rows)} dialogue rows", flush=True)

    sigs = detect_rephrase(rows, pj_slug="")
    print(f"[a2-replay] detect_rephrase (post-A2, is_machinery_prompt 委譲済み) produced "
          f"{len(sigs)} rephrase signals", flush=True)

    dump: List[Dict[str, Any]] = []
    suspects = 0
    for s in sigs:
        prov = s.provenance
        prev_text = str(prov.get("prev_text", ""))
        text = str(prov.get("text", ""))
        is_suspect = reference_dispatch_suspect(prev_text) or reference_dispatch_suspect(text)
        if is_suspect:
            suspects += 1
        dump.append({
            "pj_slug": s.pj_slug,
            "session_id": s.session_id,
            "similarity": prov.get("similarity"),
            "prev_text": prev_text,
            "text": text,
            "reference_dispatch_suspect": is_suspect,
        })

    print(f"[a2-replay] reference_dispatch_suspect (目視補助フィルタ) hits: {suspects}/{len(sigs)}",
          flush=True)
    for i, d in enumerate(dump):
        flag = "SUSPECT" if d["reference_dispatch_suspect"] else "ok"
        print(f"  [{i}] [{flag}] pj={d['pj_slug']} sim={d['similarity']} "
              f"text={d['text'][:100]!r}", flush=True)

    out_path = Path(args.out) if args.out else (REPO / "scripts" / "bench" / "a2_dispatch_replay_result.json")
    out_path.write_text(
        json.dumps({
            "since": since,
            "row_count": len(rows),
            "signal_count": len(sigs),
            "reference_dispatch_suspect_count": suspects,
            "signals": dump,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[a2-replay] wrote result to {out_path}", flush=True)


if __name__ == "__main__":
    main()
