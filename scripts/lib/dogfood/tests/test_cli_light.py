"""dogfood.cli の ``--layer light`` モードのユニットテスト。

light = 高速・非ブロッキング pre-push 用の層構成（Layer1a 不変 + Layer2 + Layer3）。
重い Layer1b drain（約3分）と ingest E2E を**走らせない**ことを構造的に封じる。
実層関数は mock し、cli のオーケストレーション（どの層を呼ぶか / exit code）だけ検証する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from dogfood import cli  # noqa: E402


def _patch_layers(monkeypatch, tmp_path, *, inv_status="pass", l2_failures=None, l3_fail=0):
    """light モードが触る3経路を mock。run_layer1（フル）/ _run_drain が呼ばれたら即失敗。"""
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"phases": {}}), encoding="utf-8")

    def fake_inv(repo_root, out_dir=None, **kw):
        return {"status": inv_status, "diff": {"added": [], "removed": [], "modified": []},
                "detail": "mock invariance", "result_path": str(result_path)}

    def boom_full_layer1(*a, **k):  # フル Layer1（ingest+1b）が呼ばれたら設計違反
        raise AssertionError("light モードでフル run_layer1 を呼んではならない")

    def boom_drain(*a, **k):
        raise AssertionError("light モードで Layer1b drain を呼んではならない")

    monkeypatch.setattr(cli.layer1, "check_dry_run_invariance", fake_inv)
    monkeypatch.setattr(cli.layer1, "run_layer1", boom_full_layer1)
    monkeypatch.setattr(cli.layer1, "check_store_diff_1b", boom_drain)
    monkeypatch.setattr(cli.invariants, "run_all",
                        lambda result: [{"check": "required_keys", "failures": l2_failures or []}])
    monkeypatch.setattr(cli.layer3, "run_layer3",
                        lambda repo_root: {"summary": {"pass": 1, "fail": l3_fail, "skip": 0}, "skills": []})
    return result_path


def test_light_runs_1a_2_3_and_skips_heavy(monkeypatch, tmp_path, capsys):
    _patch_layers(monkeypatch, tmp_path)
    rc = cli.main(["--layer", "light", "--json", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["layer"] == "light"
    # Layer1 は 1a 不変 1 件のみ（ingest_e2e / 1b_store_diff は無い）
    names = [c["name"] for c in report["layer1"]["checks"]]
    assert names == ["1a_dry_run_invariance"], names
    assert "layer2" in report and "layer3" in report


def test_light_red_on_layer1a_fail(monkeypatch, tmp_path):
    _patch_layers(monkeypatch, tmp_path, inv_status="fail")
    rc = cli.main(["--layer", "light", "--out-dir", str(tmp_path / "out")])
    assert rc == 1


def test_light_red_on_layer3_fail(monkeypatch, tmp_path):
    _patch_layers(monkeypatch, tmp_path, l3_fail=2)
    rc = cli.main(["--layer", "light", "--out-dir", str(tmp_path / "out")])
    assert rc == 1


def test_light_red_on_layer2_fail(monkeypatch, tmp_path):
    _patch_layers(monkeypatch, tmp_path, l2_failures=[{"detail": "missing key"}])
    rc = cli.main(["--layer", "light", "--out-dir", str(tmp_path / "out")])
    assert rc == 1
