"""dogfood.layer1 のユニットテスト（#496 Layer 1 オーケストレーション）。

evolve subprocess 起動は重いので mock し、snapshot 差分判定のロジックだけ検証する。
実機 dry-run は bin/rl-dogfood-gate --layer 1 で別途確認する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402

from dogfood import layer1  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    isolate_home(monkeypatch, tmp_path / "_home")


def test_dry_run_invariance_green_when_no_change(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.json").write_text("x", encoding="utf-8")

    def fake_run_evolve_dry(repo_root, output_path, env=None):
        # dry-run が何も書かないケース
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_evolve_dry)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=data_dir, out_dir=tmp_path / "out"
    )
    assert res["status"] == "pass", res
    assert res["diff"]["modified"] == []


def test_dry_run_invariance_red_when_files_change(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.json").write_text("x", encoding="utf-8")

    def fake_run_evolve_dry(repo_root, output_path, env=None):
        # dry-run が DATA_DIR を書き換えるケース（#491 型）
        (data_dir / "a.json").write_text("MUTATED", encoding="utf-8")
        (data_dir / "new-marker").write_text("", encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_evolve_dry)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=data_dir, out_dir=tmp_path / "out"
    )
    assert res["status"] == "fail"
    assert "a.json" in res["diff"]["modified"]
    assert "new-marker" in res["diff"]["added"]


def test_dry_run_invariance_errors_on_nonzero_exit(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    def fake_run_evolve_dry(repo_root, output_path, env=None):
        return {"returncode": 2, "stderr": "boom"}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_evolve_dry)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=data_dir, out_dir=tmp_path / "out"
    )
    assert res["status"] == "error"


def test_check_store_diff_1b_not_implemented(tmp_path):
    res = layer1.check_store_diff_1b(repo_root=tmp_path)
    assert res["status"] == "skip"
    assert "484" in res["detail"]
