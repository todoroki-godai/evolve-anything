"""utterance_archive.ingest — transcript jsonl → utterances.db の増分取り込み（#430）。

データソース: ``~/.claude/projects/<pj_dir>/*.jsonl``（+ ``*/subagents/*.jsonl``）。
最上位 1 connection（DuckDB checkpoint pitfall 準拠）。

増分 ingest:
- ingest_state(source_path, mtime, line_offset) と突合し、新規/追記分のみ parse。
- mtime 同一かつ offset 既達ならスキップ。全量再走査は初回 backfill のみ。
- 論理 UNIQUE index があるため、state が壊れて再走査しても重複は入らない（冪等）。

完走時に staleness marker（last_ingest_at）を DATA_DIR に書く。
DATA_DIR は ADR-042 resolver（rl_common.resolve_data_dir）経由で解決する。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import store as _store
from .extractor import extract_utterances, pj_slug_from_dir_name


def default_db_path() -> Path:
    """utterances.db の正準パスを ADR-042 resolver 経由で解決する。"""
    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / "utterances.db"


def _file_line_count(path: Path) -> int:
    """ファイルの行数を数える（line_offset の更新に使う）。"""
    n = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for _ in f:
                n += 1
    except OSError:
        return 0
    return n


def ingest_pj_dir(
    pj_dir: Path,
    con: Any,
    state: Dict[str, tuple],
    days: Optional[int] = None,
    max_files: Optional[int] = None,
    progress: bool = False,
) -> dict:
    """1 PJ ディレクトリを ingest（増分）。

    Args:
        con:       共有 DuckDB connection
        state:     get_ingest_state() の結果 {source_path: (mtime, line_offset)}
        days:      mtime フィルタ（None = 無制限）
        max_files: 取り込みファイル数の上限（backfill bench 用サンプリング）
    """
    pj_dir = Path(pj_dir)
    pj_slug = pj_slug_from_dir_name(pj_dir.name)
    inserted = 0
    files_processed = 0
    skipped_files = 0

    cutoff = None
    if days is not None and days >= 0:
        cutoff = time.time() - days * 86400

    candidates = sorted(pj_dir.glob("*.jsonl")) + sorted(pj_dir.glob("*/subagents/*.jsonl"))
    for jsonl in candidates:
        if max_files is not None and files_processed >= max_files:
            break
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue

        source_path = str(jsonl.resolve())
        prev_mtime, prev_offset = state.get(source_path, (None, 0))

        # mtime 同一かつ offset 既達ならスキップ（増分 ingest の高速経路）。
        if prev_mtime is not None and mtime <= prev_mtime:
            skipped_files += 1
            continue

        start_line = prev_offset if prev_mtime is not None else 0
        utts = list(extract_utterances(jsonl, pj_slug=pj_slug, start_line=start_line))
        if utts:
            inserted += _store.insert_utterances(con, utts)

        new_offset = _file_line_count(jsonl)
        _store.upsert_ingest_state(con, source_path, mtime=mtime, line_offset=new_offset)
        files_processed += 1

        if progress:
            sys.stderr.write(
                f"  [{pj_slug}] {jsonl.name}: +{len(utts)} utterances "
                f"(inserted_total={inserted})\n"
            )
            sys.stderr.flush()

    return {
        "inserted": inserted,
        "files_processed": files_processed,
        "skipped_files": skipped_files,
        "pj_slug": pj_slug,
    }


def ingest_all_projects(
    projects_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
    days: Optional[int] = None,
    max_files: Optional[int] = None,
    progress: bool = True,
) -> dict:
    """全 PJ を ingest。1 connection を共有し、完走時に staleness marker を書く。

    Args:
        projects_root: default ``~/.claude/projects``
        db_path:       default は ADR-042 resolver 経由の DATA_DIR / utterances.db
        days:          mtime フィルタ（None = 無制限。backfill は None）
        max_files:     PJ ごとのファイル上限（bench サンプリング）
    """
    root = Path(projects_root) if projects_root else Path.home() / ".claude" / "projects"
    db_path = Path(db_path) if db_path is not None else default_db_path()

    if not _store.HAS_DUCKDB:
        return {"inserted": 0, "files_processed": 0, "projects": 0, "duckdb": False}
    if not root.exists():
        return {"inserted": 0, "files_processed": 0, "projects": 0}

    pj_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    agg = {
        "inserted": 0, "files_processed": 0, "skipped_files": 0,
        "projects": len(pj_dirs),
    }
    t0 = time.perf_counter()
    with _store.connection(db_path) as con:
        if con is None:
            return {"inserted": 0, "files_processed": 0, "projects": 0, "duckdb": False}
        state = _store.get_ingest_state(con)
        for i, pj_dir in enumerate(pj_dirs, 1):
            res = ingest_pj_dir(
                pj_dir, con, state, days=days, max_files=max_files, progress=progress
            )
            agg["inserted"] += res["inserted"]
            agg["files_processed"] += res["files_processed"]
            agg["skipped_files"] += res["skipped_files"]
            if progress:
                sys.stderr.write(
                    f"[{i}/{len(pj_dirs)}] {res['pj_slug']}: "
                    f"inserted={res['inserted']} files={res['files_processed']} "
                    f"skipped={res['skipped_files']}\n"
                )
                sys.stderr.flush()

    # 完走時に staleness marker を書く（marker 不在=未 ingest=stale の解釈と整合）。
    _store.write_last_ingest_at(db_path.parent)
    agg["elapsed_s"] = time.perf_counter() - t0
    return agg
