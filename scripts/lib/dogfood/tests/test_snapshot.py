"""dogfood.snapshot のユニットテスト（#496 Layer 1a）。

合成 fixture のみ。実環境 DATA_DIR には触れない（autouse HOME 隔離）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402

from dogfood import snapshot  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    isolate_home(monkeypatch, tmp_path / "_home")


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_snapshot_empty_dir(tmp_path: Path) -> None:
    snap = snapshot.snapshot_dir(tmp_path)
    assert snap == {}


def test_snapshot_missing_dir_returns_empty(tmp_path: Path) -> None:
    snap = snapshot.snapshot_dir(tmp_path / "does-not-exist")
    assert snap == {}


def test_snapshot_hashes_files_recursively(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", "hello")
    _write(tmp_path / "sub" / "b.jsonl", "world")
    snap = snapshot.snapshot_dir(tmp_path)
    assert set(snap.keys()) == {"a.json", "sub/b.jsonl"}
    # 値は 64 hex の SHA256
    for h in snap.values():
        assert len(h) == 64
        int(h, 16)


def test_diff_no_change(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", "x")
    before = snapshot.snapshot_dir(tmp_path)
    after = snapshot.snapshot_dir(tmp_path)
    diff = snapshot.diff_snapshots(before, after)
    assert diff == {"added": [], "removed": [], "modified": []}
    assert snapshot.is_unchanged(diff) is True


def test_diff_detects_modified(tmp_path: Path) -> None:
    f = tmp_path / "a.json"
    _write(f, "x")
    before = snapshot.snapshot_dir(tmp_path)
    _write(f, "y")
    after = snapshot.snapshot_dir(tmp_path)
    diff = snapshot.diff_snapshots(before, after)
    assert diff["modified"] == ["a.json"]
    assert diff["added"] == [] and diff["removed"] == []
    assert snapshot.is_unchanged(diff) is False


def test_diff_detects_added_and_removed(tmp_path: Path) -> None:
    _write(tmp_path / "keep.json", "k")
    f_remove = tmp_path / "gone.json"
    _write(f_remove, "g")
    before = snapshot.snapshot_dir(tmp_path)
    f_remove.unlink()
    _write(tmp_path / "new.json", "n")
    after = snapshot.snapshot_dir(tmp_path)
    diff = snapshot.diff_snapshots(before, after)
    assert diff["added"] == ["new.json"]
    assert diff["removed"] == ["gone.json"]
    assert diff["modified"] == []
    assert snapshot.is_unchanged(diff) is False
