"""DATA_DIR の SHA256 スナップショット比較（#496 Layer 1a）。

dry-run の evolve が実環境 DATA_DIR を一切書き換えないことを assert するために、
実行前後の全ファイルを SHA256 でスナップショットし差分を取る。

前例: learning_dryrun_verification_blind_spot（#400）「完了基準は store 差分」の体系化。
#491 で dry-run が 4 ファイル（evolve_pending marker / audit-history.jsonl /
skill-evolve-cache.json / evolve-state.json）を書き換えることが実測済みで、本ゲートが
これを赤として検出するのが受け入れ基準。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

# スナップショット対象から外すノイズ（環境ごとに揺れる / dry-run と無関係）。
_IGNORE_NAMES = {".DS_Store"}
_IGNORE_DIR_PARTS = {"__pycache__"}


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name in _IGNORE_NAMES:
            continue
        if any(part in _IGNORE_DIR_PARTS for part in p.relative_to(root).parts):
            continue
        yield p


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_dir(root: Path) -> Dict[str, str]:
    """``root`` 配下の全ファイルを相対パス→SHA256 の dict で返す。

    存在しない dir は空 dict（差分計算の対称性のため例外にしない）。
    """
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return {}
    snap: Dict[str, str] = {}
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        try:
            snap[rel] = _hash_file(p)
        except OSError:
            # 走査中に消えた一時ファイル等はスキップ（diff の安定性優先）。
            continue
    return snap


def diff_snapshots(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, List[str]]:
    """2 スナップショットの added / removed / modified を返す（相対パスソート済み）。"""
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified = sorted(k for k in before_keys & after_keys if before[k] != after[k])
    return {"added": added, "removed": removed, "modified": modified}


def is_unchanged(diff: Dict[str, List[str]]) -> bool:
    """diff が完全に空（差分ゼロ）なら True。"""
    return not (diff.get("added") or diff.get("removed") or diff.get("modified"))
