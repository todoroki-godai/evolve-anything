"""Layer 1: 実 PJ utterance ingest E2E check（#496, 35秒テスト移設）。

元は ``scripts/lib/tests/test_utterance_ingest.py::test_real_pj_e2e``（@pytest.mark.real_home,
直列 35 秒）。pytest スイートから本ゲートへ移設し、pytest 非依存の check 関数に書き直した。
assert の検証内容（wall time / DB size / rows）は維持する（transcript-store-bench ルール）。

書き込み先は tmp dir のみ（実 DATA_DIR に触れない）。実 transcript パスは読むだけ。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional


def find_real_rl_anything_pj() -> Optional[Path]:
    """実 ``~/.claude/projects`` から evolve-anything 本体 PJ ディレクトリを 1 つ返す。

    worktrees サフィックスの PJ は除外（本体の transcript を読むため）。
    """
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None
    cands = sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name.endswith("evolve-anything") and not p.name.endswith("worktrees")
    )
    return cands[0] if cands else None


def check_ingest_e2e(
    pj_dir: Path,
    db_dir: Path,
    max_seconds: float = 60.0,
) -> Dict[str, Any]:
    """指定 PJ ディレクトリの transcript で ingest を完走させ検証する。

    返り値: ``{"status": "pass"|"fail"|"skip", "detail": str, "rows": int,
               "elapsed_sec": float, "db_size_bytes": int}``
    """
    try:
        from utterance_archive import ingest as uingest
        from utterance_archive import query as uquery
        from utterance_archive import store as ustore
    except Exception as e:  # noqa: BLE001
        return {"status": "skip", "detail": f"utterance_archive import 不可: {e!r}"}

    if not getattr(ustore, "HAS_DUCKDB", False):
        return {"status": "skip", "detail": "DuckDB 未インストール"}

    pj_dir = Path(pj_dir)
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)

    # tmp に projects root を作り、対象 PJ ディレクトリを 1 つだけ symlink で見せる
    fake_root = db_dir / "projects"
    fake_root.mkdir(exist_ok=True)
    link = fake_root / pj_dir.name
    if not link.exists():
        link.symlink_to(pj_dir)

    db = db_dir / "utterances.db"
    t0 = time.perf_counter()
    res = uingest.ingest_all_projects(projects_root=fake_root, db_path=db, progress=False)
    elapsed = time.perf_counter() - t0

    out: Dict[str, Any] = {
        "elapsed_sec": round(elapsed, 2),
        "inserted": res.get("inserted", 0),
        "db_size_bytes": db.stat().st_size if db.exists() else 0,
    }

    if elapsed >= max_seconds:
        return {**out, "status": "fail", "detail": f"ingest が遅すぎ: {elapsed:.1f}s >= {max_seconds}s", "rows": 0}
    if not (db.exists() and db.stat().st_size > 0):
        return {**out, "status": "fail", "detail": "DB が空 / 未生成", "rows": 0}

    # PJ slug は cwd 由来。本体 PJ なら "evolve-anything" で引ける。
    rows = uquery.query_utterances("evolve-anything", db_path=db)
    out["rows"] = len(rows)
    if len(rows) == 0:
        return {**out, "status": "fail", "detail": "実 PJ から発話が 1 件も取れない（抽出バグ）"}
    if not all(r["source_kind"] == "dialogue" for r in rows):
        return {**out, "status": "fail", "detail": "source_kind デフォルトが dialogue でない"}
    return {**out, "status": "pass", "detail": f"{len(rows)} rows in {elapsed:.1f}s"}


def check_real_pj_ingest(db_dir: Path) -> Dict[str, Any]:
    """実 evolve-anything PJ を見つけて ingest E2E を回す（実機ゲート用ラッパ）。"""
    pj = find_real_rl_anything_pj()
    if pj is None:
        return {"status": "skip", "detail": "実 evolve-anything transcript が見つからない"}
    return check_ingest_e2e(pj_dir=pj, db_dir=db_dir)
