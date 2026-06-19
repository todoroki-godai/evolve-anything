#!/usr/bin/env python3
"""issue #28 — 実 PJ token_usage ingest E2E ベンチ。

opt-in: `pytest -m bench_ingest -s scripts/tests/bench_ingest.py`

PJ rule (.claude/rules/transcript-store-bench.md) 準拠:
- 実機 1 PJ E2E ベンチで wall time/DB size/row 数を assertion
- 進捗 print(..., flush=True) (各 PJ 計測値)
- timeout で暴走回避
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = str(_REPO_ROOT / "scripts" / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)


_REAL_PJ = (
    Path.home()
    / ".claude"
    / "projects"
    / "-Users-todoroki-tools-evolve-anything"
)


def _setup_store(tmp_path, monkeypatch):
    import token_usage_store as tus
    import token_usage_ingest as tui
    monkeypatch.setattr(tus, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tus, "USAGE_DB", tmp_path / "token_usage.db")
    monkeypatch.setattr(tus, "USAGE_JSONL", tmp_path / "token_usage.jsonl")
    monkeypatch.setattr(tui, "_store", tus)
    return tus, tui


@pytest.mark.bench_ingest
def test_real_pj_single_under_60s(tmp_path, monkeypatch):
    """evolve-anything PJ 1 個 / --days 7 / 60 秒以内 + 2 回目 30 秒以内 (design doc 主要 success criteria)。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    if not _REAL_PJ.exists():
        pytest.skip(f"real PJ not found: {_REAL_PJ}")

    tus, tui = _setup_store(tmp_path, monkeypatch)

    print(f"\n=== Bench (1 PJ): {_REAL_PJ.name} (--days 7) ===", flush=True)

    with tus.connection() as con:
        t0 = time.perf_counter()
        res1 = tui.ingest_pj_dir(_REAL_PJ, days=7, con=con, progress=False)
        t1 = time.perf_counter() - t0
        print(
            f"PASS-1: {t1:.2f}s inserted={res1['inserted']} files={res1['files_processed']}",
            flush=True,
        )
        print(
            f"  timings: glob={res1['timings']['glob_s']:.2f}s "
            f"parse={res1['timings']['parse_s']:.2f}s "
            f"commit={res1['timings']['commit_s']:.2f}s "
            f"progress={res1['timings']['progress_s']:.2f}s",
            flush=True,
        )

        t0 = time.perf_counter()
        res2 = tui.ingest_pj_dir(_REAL_PJ, days=7, con=con, progress=False)
        t2 = time.perf_counter() - t0
        print(
            f"PASS-2: {t2:.2f}s inserted={res2['inserted']} files={res2['files_processed']}",
            flush=True,
        )

    db_size = (tmp_path / "token_usage.db").stat().st_size
    rows = tus.query("SELECT COUNT(*) FROM token_usage")[0][0]
    print(f"DB: {db_size:,} bytes / {rows:,} rows = {db_size/max(rows,1):.0f} bytes/row", flush=True)

    parse_s = res1["timings"]["parse_s"]
    commit_s = res1["timings"]["commit_s"]
    ratio = (parse_s / commit_s) if commit_s > 0 else float("inf")
    print(f"VERDICT: parse/commit={ratio:.2f} → "
          f"{'parse-bound (consider byte-offset)' if ratio >= 2.0 else 'commit-bound (Approach B sufficient)'}",
          flush=True)
    print(
        f"\nBench: evolve-anything --days 7 = {t1:.1f}s (incr {t2:.1f}s) / "
        f"DB {db_size/(1024*1024):.1f} MB / {rows:,} rows",
        flush=True,
    )

    assert t1 < 60.0, f"PASS-1 {t1:.2f}s exceeds 60s budget"
    assert t2 < 30.0, f"PASS-2 (incremental) {t2:.2f}s exceeds 30s budget"
    if rows > 0:
        assert db_size < rows * 1024, (
            f"DB size {db_size/1024:.0f}KB > rows×1KB ({rows}KB) — write amplification regression"
        )
    assert res2["inserted"] == 0, "PASS-2 should have no new rows (incremental)"


@pytest.mark.bench_ingest
def test_real_pj_all_smoke(tmp_path, monkeypatch):
    """全 PJ smoke: 完走することと write amplification 不在を確認 (assertion は緩め)。"""
    try:
        import duckdb  # noqa
    except ImportError:
        pytest.skip("duckdb not installed")
    if not _REAL_PJ.exists():
        pytest.skip(f"real PJ not found: {_REAL_PJ}")

    tus, tui = _setup_store(tmp_path, monkeypatch)
    projects_root = _REAL_PJ.parent

    print(f"\n=== Bench (ALL PJ smoke): {projects_root} (--days 7) ===", flush=True)
    t0 = time.perf_counter()
    res = tui.ingest_all_projects(claude_projects_root=projects_root, days=7, progress=False)
    t1 = time.perf_counter() - t0
    db_size = (tmp_path / "token_usage.db").stat().st_size
    rows = tus.query("SELECT COUNT(*) FROM token_usage")[0][0]
    print(
        f"ALL-PJ: {t1:.2f}s inserted={res['inserted']} files={res['files_processed']} "
        f"projects={res['projects']} DB={db_size/(1024*1024):.1f}MB rows={rows:,}",
        flush=True,
    )

    # smoke: 完走 + DB size 健全。time budget は 1 PJ test 側で担保
    assert res["files_processed"] >= 0
    if rows > 0:
        assert db_size < rows * 1024
