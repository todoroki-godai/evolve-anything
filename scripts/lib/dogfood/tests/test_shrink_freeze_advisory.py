"""dogfood.cli の Shrink Freeze 非ブロッキング advisory のテスト（#379 Step 1 修正2）。

実効ゲートは CI の契約テスト（scripts/lib/tests/test_shrink_freeze.py、blocking）。
`--layer light`（および `all`）に組み込む本 advisory は Layer1/2/3 と異なり **exit code に
一切影響しない**（skill_reachability / doc_budget と同型）。push 前に新設を早期検知する
だけの警告であることをここで固定する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from dogfood import cli  # noqa: E402


def _patch_layers(monkeypatch, tmp_path, *, inv_status="pass", l3_fail=0):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"phases": {}}), encoding="utf-8")

    def fake_inv(repo_root, out_dir=None, **kw):
        return {"status": inv_status, "diff": {"added": [], "removed": [], "modified": []},
                "detail": "mock invariance", "result_path": str(result_path)}

    monkeypatch.setattr(cli.layer1, "check_dry_run_invariance", fake_inv)
    monkeypatch.setattr(cli.invariants, "run_all", lambda result: [{"check": "required_keys", "failures": []}])
    monkeypatch.setattr(
        cli.layer3, "run_layer3",
        lambda repo_root: {"summary": {"pass": 1, "fail": l3_fail, "skip": 0}, "skills": []},
    )
    monkeypatch.setattr(cli, "_run_skill_reachability_advisory", lambda repo_root: {"applicable": False})
    monkeypatch.setattr(cli, "_run_doc_budget_advisory", lambda repo_root: {"applicable": False})


def test_run_advisory_returns_non_applicable_on_import_error(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "shrink_freeze":
            raise ImportError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = cli._run_shrink_freeze_advisory(tmp_path)
    assert result == {"applicable": False}


def test_run_advisory_clean_when_within_frozen_snapshot(tmp_path):
    """現行 repo の実際の登録集合は凍結スナップショット範囲内（新設なし）であること。"""
    result = cli._run_shrink_freeze_advisory(tmp_path)
    assert result["applicable"] is True
    assert result["frozen"] is True
    assert result["violations"] == []


def test_run_advisory_reports_violation_when_new_key_detected(monkeypatch, tmp_path):
    import shrink_freeze as sf
    import store_registry

    monkeypatch.setattr(
        store_registry, "declared_store_names",
        lambda: list(sf.FROZEN_STORES) + ["brand_new_store.jsonl"],
    )
    result = cli._run_shrink_freeze_advisory(tmp_path)
    assert result["applicable"] is True
    kinds = {v["kind"] for v in result["violations"]}
    assert "store" in kinds


def test_run_advisory_no_violations_when_unfrozen(monkeypatch, tmp_path):
    import shrink_freeze as sf
    import store_registry

    monkeypatch.setattr(sf, "SHRINK_FREEZE_ACTIVE", False)
    monkeypatch.setattr(
        store_registry, "declared_store_names",
        lambda: list(sf.FROZEN_STORES) + ["brand_new_store.jsonl"],
    )
    result = cli._run_shrink_freeze_advisory(tmp_path)
    assert result["applicable"] is True
    assert result["frozen"] is False
    assert result["violations"] == []


def test_print_advisory_clean(capsys):
    cli._print_shrink_freeze_advisory({"applicable": True, "frozen": True, "violations": []})
    out = capsys.readouterr().out
    assert "✓" in out


def test_print_advisory_non_applicable(capsys):
    cli._print_shrink_freeze_advisory({"applicable": False})
    out = capsys.readouterr().out
    assert "非該当" in out


def test_print_advisory_unfrozen(capsys):
    cli._print_shrink_freeze_advisory({"applicable": True, "frozen": False, "violations": []})
    out = capsys.readouterr().out
    assert "解除中" in out


def test_print_advisory_warns_with_evidence(capsys):
    cli._print_shrink_freeze_advisory({
        "applicable": True, "frozen": True,
        "violations": [{"kind": "store", "detail": "store: 新規追加を検出しました ['x.jsonl']"}],
    })
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "x.jsonl" in out


def test_light_layer_includes_advisory_without_affecting_exit_code(monkeypatch, tmp_path, capsys):
    """新設が検出されても light の exit code は変わらない（非ブロッキング）。"""
    _patch_layers(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_shrink_freeze_advisory", lambda repo_root: {
        "applicable": True, "frozen": True,
        "violations": [{"kind": "store", "detail": "store: 新規追加を検出しました ['x.jsonl']"}],
    })
    rc = cli.main(["--layer", "light", "--json", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["shrink_freeze"]["violations"][0]["kind"] == "store"


def test_all_layer_prints_advisory_section(monkeypatch, tmp_path, capsys):
    _patch_layers(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_layer1", lambda repo_root, out_dir: {"checks": [], "result_path": None})
    monkeypatch.setattr(cli, "_run_shrink_freeze_advisory", lambda repo_root: {
        "applicable": True, "frozen": True, "violations": [],
    })
    rc = cli.main(["--layer", "all", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Advisory: Shrink Freeze" in out
