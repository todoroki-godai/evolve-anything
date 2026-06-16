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
        # dry-run が隔離コピー先（CLAUDE_PLUGIN_DATA）を書き換えるケース（#491 型）。
        # 新しい隔離コピー方式では env["CLAUDE_PLUGIN_DATA"] がコピー先パスを指す。
        isolated = Path(env["CLAUDE_PLUGIN_DATA"]) if env and "CLAUDE_PLUGIN_DATA" in env else data_dir
        (isolated / "a.json").write_text("MUTATED", encoding="utf-8")
        (isolated / "new-marker").write_text("", encoding="utf-8")
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


def test_check_store_diff_1b_implemented_pass(monkeypatch, tmp_path):
    """Layer 1b は #518 で実装済み（NotImplemented の skip 枠を解消）。

    drain サブプロセスを mock し、weak_signals_persisted(dry_run=False) と
    isolated copy への書込で pass になることを確認する。詳細は
    test_layer1b_store_diff.py。ここでは「skip でなく実 check になった」ことを封じる。
    """
    import json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("{}", encoding="utf-8")
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps({"phases": {}, "evolve_decisions": {"pending": []}}), encoding="utf-8"
    )

    def fake_drain(repo_root, result_json, env=None):
        data = Path(env["CLAUDE_PLUGIN_DATA"])
        (data / "weak_signals.jsonl").write_text('{"channel": "rephrase"}\n', encoding="utf-8")
        summary = {"weak_signals_persisted": {"written": 1, "dry_run": False}}
        return {"returncode": 0, "stdout": json.dumps(summary), "stderr": ""}

    monkeypatch.setattr(layer1, "_run_drain", fake_drain)
    res = layer1.check_store_diff_1b(
        repo_root=tmp_path / "repo",
        data_dir=data_dir,
        out_dir=tmp_path / "out",
        result_json=result_path,
    )
    assert res["status"] == "pass", res
    assert res["weak_signals_persisted"]["dry_run"] is False
