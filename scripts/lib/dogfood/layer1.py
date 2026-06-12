"""Layer 1: dogfood E2E（#496）。

(1a) dry-run 不変: DATA_DIR の全ファイル SHA256 スナップショット → 素の python 起動で
     ``evolve.py --dry-run --output <tmp>`` → 再スナップショットで差分ゼロを assert。
     #491 の 4 ファイル書換を赤として検出するのが受け入れ基準（bypass は作らない）。
(1b) store 差分（書かれるべきものが書かれる方向）: 非 dry-run は実環境を汚すため Wave 0 では
     未実装。NotImplemented 枠だけ予約し #484 修正後に実装する。

dry-run の result JSON は Layer 2（invariants）に渡せるよう返す。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import snapshot
from . import ingest_check


def _default_data_dir() -> Path:
    """正準 DATA_DIR（env 非依存固定: ~/.claude/rl-anything）。"""
    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "rl-anything"


def _sys_path_dirs(repo_root: Path) -> List[Path]:
    """素の起動経路で evolve.py が必要とする sys.path（conftest 下駄なし）。"""
    return [
        repo_root / "scripts" / "lib",
        repo_root / "scripts",
        repo_root / "skills" / "evolve" / "scripts",
        repo_root / "skills" / "audit" / "scripts",
    ]


def _run_evolve_dry_run(repo_root: Path, output_path: Path, env: Optional[dict] = None) -> Dict[str, Any]:
    """``evolve.py --dry-run --output <path>`` を素の python subprocess で起動する。

    PYTHONPATH のみで sys.path を構成し、conftest の補完を一切受けない（ユーザーと同じ起動経路）。
    LLM は dry-run 経路では呼ばれない（評価系は --dry-run で skip / cache 参照）。
    """
    evolve_py = repo_root / "skills" / "evolve" / "scripts" / "evolve.py"
    run_env = dict(os.environ if env is None else env)
    run_env["PYTHONPATH"] = os.pathsep.join(str(p) for p in _sys_path_dirs(repo_root))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(evolve_py), "--dry-run", "--project-dir", str(repo_root), "--output", str(output_path)],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=str(repo_root),
        timeout=600,
    )
    return {"returncode": proc.returncode, "stderr": proc.stderr, "stdout": proc.stdout}


def check_dry_run_invariance(
    repo_root: Path,
    data_dir: Optional[Path] = None,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """dry-run evolve が DATA_DIR を一切書き換えないことを検査（Layer 1a）。

    返り値: ``{"status": "pass"|"fail"|"error", "diff": {...}, "detail": str,
               "result_path": str|None}``
    """
    repo_root = Path(repo_root)
    data_dir = Path(data_dir) if data_dir is not None else _default_data_dir()
    out_dir = Path(out_dir) if out_dir is not None else (Path("/tmp") / "rl-dogfood-gate")
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "evolve-dryrun-result.json"

    before = snapshot.snapshot_dir(data_dir)
    run = _run_evolve_dry_run(repo_root, result_path)
    after = snapshot.snapshot_dir(data_dir)
    diff = snapshot.diff_snapshots(before, after)

    if run.get("returncode", 1) != 0:
        tail = (run.get("stderr") or "").strip().splitlines()
        return {
            "status": "error",
            "diff": diff,
            "detail": f"evolve --dry-run exit {run.get('returncode')}: {tail[-1] if tail else ''}",
            "result_path": str(result_path) if result_path.exists() else None,
        }

    if snapshot.is_unchanged(diff):
        return {"status": "pass", "diff": diff, "detail": "DATA_DIR 不変", "result_path": str(result_path)}

    changed = []
    for kind in ("added", "removed", "modified"):
        changed += [f"{kind}:{p}" for p in diff[kind]]
    return {
        "status": "fail",
        "diff": diff,
        "detail": f"dry-run が DATA_DIR を書き換えた: {changed}",
        "result_path": str(result_path) if result_path.exists() else None,
    }


def check_store_diff_1b(repo_root: Path) -> Dict[str, Any]:
    """Layer 1b: 「書かれるべきものが書かれる」方向の store 差分検査。

    非 dry-run evolve は実環境 DATA_DIR を汚すため Wave 0 では実装しない。
    #484（配線の死）修正後に、隔離 HOME+DATA_DIR で非 dry-run を 1 周し
    weak_signals 4 チャネル / usage / corrections 等の store 差分を assert する。
    """
    return {
        "status": "skip",
        "detail": "Layer 1b は #484 修正後に実装予定（非 dry-run store 差分 / 実環境汚染回避のため Wave 0 は未実装）",
    }


def run_layer1(repo_root: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Layer 1 全チェックを回す。

    返り値: ``{"checks": [...], "result_path": str|None}``。result_path は Layer 2 が
    読む dry-run result JSON のパス（成功時のみ）。
    """
    repo_root = Path(repo_root)
    out_dir = Path(out_dir) if out_dir is not None else (Path("/tmp") / "rl-dogfood-gate")
    checks: List[Dict[str, Any]] = []

    inv = check_dry_run_invariance(repo_root, out_dir=out_dir)
    checks.append({"name": "1a_dry_run_invariance", **inv})

    ingest_res = ingest_check.check_real_pj_ingest(db_dir=out_dir / "ingest")
    checks.append({"name": "1_ingest_e2e", **ingest_res})

    b1 = check_store_diff_1b(repo_root)
    checks.append({"name": "1b_store_diff", **b1})

    return {"checks": checks, "result_path": inv.get("result_path")}
