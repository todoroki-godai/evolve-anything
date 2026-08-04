"""dogfood.cli の Doc Budget 非ブロッキング advisory のテスト（#319）。

`--layer light`（および `all`）に組み込む advisory は Layer1/2/3 と異なり **exit code に
一切影響しない**（skill_reachability advisory と同型）。cli のオーケストレーション
（結果が report dict に載ること / 予算超過が有っても exit code が変わらないこと）だけを検証する。
決定論ロジック自体は scripts/lib/tests/test_doc_budget.py でカバー済みなのでここでは mock する。
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


def test_run_advisory_returns_non_applicable_on_import_error(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "doc_budget":
            raise ImportError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = cli._run_doc_budget_advisory(tmp_path)
    assert result == {"applicable": False}


def test_run_advisory_serializes_findings(monkeypatch, tmp_path):
    import doc_budget

    fake_report = doc_budget.DocBudgetReport(
        applicable=True,
        file_findings=[
            doc_budget.FileBudgetFinding(
                path="SPEC.md", byte_size=40000, must_bytes=35840, healthy_bytes=20480, severity="must"
            )
        ],
        section_findings=[
            doc_budget.SectionBudgetFinding(
                file="SPEC.md", heading="Big", byte_size=9000, file_total_bytes=40000, pct=22.5
            )
        ],
        pointer_findings=[
            doc_budget.PointerRefFinding(
                source_file="SPEC.md", link_text="ghost", raw_target="spec/ghost.md", kind="missing_file"
            )
        ],
    )
    monkeypatch.setattr(doc_budget, "check_doc_budget", lambda repo_root: fake_report)
    result = cli._run_doc_budget_advisory(tmp_path)
    assert result["applicable"] is True
    assert result["file_findings"] == [
        {"path": "SPEC.md", "byte_size": 40000, "must_bytes": 35840, "healthy_bytes": 20480, "severity": "must"}
    ]
    assert result["section_findings"] == [
        {"file": "SPEC.md", "heading": "Big", "byte_size": 9000, "pct": 22.5}
    ]
    assert result["pointer_findings"] == [
        {"source_file": "SPEC.md", "link_text": "ghost", "raw_target": "spec/ghost.md", "kind": "missing_file"}
    ]


def test_print_advisory_clean(capsys):
    cli._print_doc_budget_advisory(
        {"applicable": True, "file_findings": [], "section_findings": [], "pointer_findings": []}
    )
    out = capsys.readouterr().out
    assert "✓" in out
    assert "該当なし" in out


def test_print_advisory_non_applicable(capsys):
    cli._print_doc_budget_advisory({"applicable": False})
    out = capsys.readouterr().out
    assert "非該当" in out


def test_print_advisory_warns_with_evidence(capsys):
    cli._print_doc_budget_advisory(
        {
            "applicable": True,
            "file_findings": [
                {"path": "SPEC.md", "byte_size": 40000, "must_bytes": 35840, "healthy_bytes": 20480, "severity": "must"}
            ],
            "section_findings": [],
            "pointer_findings": [
                {"source_file": "SPEC.md", "link_text": "ghost", "raw_target": "spec/ghost.md", "kind": "missing_file"}
            ],
        }
    )
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "SPEC.md" in out
    assert "spec/ghost.md" in out


def test_light_layer_includes_advisory_without_affecting_exit_code(monkeypatch, tmp_path, capsys):
    """予算超過が検出されても light の exit code は変わらない（非ブロッキング, #319）。"""
    _patch_layers(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_doc_budget_advisory", lambda repo_root: {
        "applicable": True,
        "file_findings": [
            {"path": "SPEC.md", "byte_size": 40000, "must_bytes": 35840, "healthy_bytes": 20480, "severity": "must"}
        ],
        "section_findings": [],
        "pointer_findings": [],
    })
    rc = cli.main(["--layer", "light", "--json", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["doc_budget"]["file_findings"][0]["path"] == "SPEC.md"


def test_all_layer_prints_advisory_section(monkeypatch, tmp_path, capsys):
    _patch_layers(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_layer1", lambda repo_root, out_dir: {"checks": [], "result_path": None})
    monkeypatch.setattr(cli, "_run_doc_budget_advisory", lambda repo_root: {
        "applicable": True, "file_findings": [], "section_findings": [], "pointer_findings": [],
    })
    rc = cli.main(["--layer", "all", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Advisory: Doc Budget" in out
