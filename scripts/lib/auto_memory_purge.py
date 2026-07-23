"""auto_memory_queue の project スコープ不一致を除去する purge ユーティリティ（#206）。

auto_memory Stop hook が project_path フィルタ無しで全 PJ 共有ストア（corrections.jsonl）を
読み、他 PJ の correction を `DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue していた
事故の修復ツール。読み出し側フィルタ（auto_memory_runner）と enqueue 側の多層防御ゲート
（auto_memory_broker.enqueue）が導入された **後** も、修正前に既に書かれてしまった
レガシー汚染キューは残るため、それを検出・除去するための別モジュール（`auto_memory_broker.py`
の 800 行バジェット超過を避けるため単責務で分離。#206 で新規追加された機能であり既存呼び出し
元は無い）。

決定論・LLM 非依存・subprocess なし。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from auto_memory_broker import QUEUE_SUBDIR, read_queue
from pj_slug import record_project_match


def _write_queue_file_records(path: Path, records: List[dict]) -> None:
    """queue ファイルを records だけの内容で原子的に上書きする（tmp → rename）。"""
    new_content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def purge_mismatched_pending(data_dir: Path, dry_run: bool = True) -> Dict[str, Any]:
    """全 PJ の pending auto_memory_queue を走査し project スコープ不一致を除去する（#206）。

    #206 の enqueue reject ゲートが導入される前に書かれたキューには、他 PJ の
    project_path を持つ correction が record 内に混入している可能性がある。本関数は
    `DATA_DIR/auto_memory_queue/<slug>.jsonl` を1件ずつ走査し、各 record の corrections
    リストから ``record_project_match(correction, slug)`` が False（他 PJ 混入）と
    判定した correction を除去する。除去後に corrections が空になった record は消化
    （削除）する。

    dry_run=True（既定）: 一切書き込まない（検出のみ）。
    dry_run=False（apply）: 実際にキューファイルを書き換える。

    Args:
        data_dir: `auto_memory_queue/` の親ディレクトリ。
        dry_run: True なら書込ゼロで検出結果のみ返す。

    Returns:
        {
          "scanned_slugs": [str],   # 走査した queue ファイルの slug 一覧
          "affected_slugs": [str],  # 不一致 correction を含んでいた slug 一覧
          "rejected_count": int,    # 除去された correction 件数の総和
          "removed_records": int,   # 空になり削除された record 件数の総和
          "dry_run": bool,
        }
    """
    data_dir = Path(data_dir)
    queue_dir = data_dir / QUEUE_SUBDIR

    scanned_slugs: List[str] = []
    affected_slugs: List[str] = []
    rejected_count = 0
    removed_records = 0

    if not queue_dir.exists():
        return {
            "scanned_slugs": scanned_slugs,
            "affected_slugs": affected_slugs,
            "rejected_count": rejected_count,
            "removed_records": removed_records,
            "dry_run": dry_run,
        }

    for path in sorted(queue_dir.glob("*.jsonl")):
        slug = path.stem
        scanned_slugs.append(slug)
        records = read_queue(slug, data_dir)
        if not records:
            continue

        file_rejected = 0
        file_removed = 0
        cleaned_records: List[dict] = []
        for rec in records:
            corrections = rec.get("corrections", [])
            kept = [c for c in corrections if record_project_match(c, slug)]
            n_dropped = len(corrections) - len(kept)
            if n_dropped:
                file_rejected += n_dropped
            if not kept:
                file_removed += 1
                continue  # corrections が全滅 → record ごと消化
            if n_dropped:
                new_rec = dict(rec)
                new_rec["corrections"] = kept
                cleaned_records.append(new_rec)
            else:
                cleaned_records.append(rec)

        if file_rejected == 0 and file_removed == 0:
            continue  # このファイルはクリーン

        affected_slugs.append(slug)
        rejected_count += file_rejected
        removed_records += file_removed

        if not dry_run:
            _write_queue_file_records(path, cleaned_records)

    return {
        "scanned_slugs": scanned_slugs,
        "affected_slugs": affected_slugs,
        "rejected_count": rejected_count,
        "removed_records": removed_records,
        "dry_run": dry_run,
    }
