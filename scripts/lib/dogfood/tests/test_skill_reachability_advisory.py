"""dogfood.cli の Skill Declaration Reachability 非ブロッキング advisory のテスト（#191）。

`--layer light`（および `all`）に組み込む advisory は Layer1/2/3 と異なり **exit code に
一切影響しない**（静的解析の FP 較正コストが高いため常に警告のみ）。cli のオーケストレーション
（結果が report dict に載ること / 到達不能が有っても exit code が変わらないこと）だけを検証する。
実 detect_unreachable_declarations 自体のロジックは
`scripts/lib/tests/test_skill_declaration_reachability.py` でカバー済みなのでここでは mock する。
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


class _FakeUnreachable:
    def __init__(self, name, source, line, def_files):
        self.name = name
        self.source = source
        self.line = line
        self.def_files = def_files


class _FakeReport:
    def __init__(self, has_skills=True, unreachable=None, evaluated_count=3):
        self.has_skills = has_skills
        self.unreachable = unreachable or []
        self.evaluated_count = evaluated_count
        self.ambiguous_count = 0
        self.unresolved_count = 0


def test_run_advisory_returns_non_applicable_on_import_error(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "skill_declaration_reachability":
            raise ImportError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = cli._run_skill_reachability_advisory(tmp_path)
    assert result == {"applicable": False}


def test_run_advisory_serializes_unreachable(monkeypatch, tmp_path):
    import skill_declaration_reachability as sdr

    fake_report = _FakeReport(
        unreachable=[_FakeUnreachable("zombie_func", "skills/demo/SKILL.md", 3, ("scripts/lib/foo.py",))]
    )
    monkeypatch.setattr(sdr, "detect_unreachable_declarations", lambda repo_root: fake_report)
    result = cli._run_skill_reachability_advisory(tmp_path)
    assert result["applicable"] is True
    assert result["evaluated_count"] == 3
    assert result["unreachable"] == [
        {"name": "zombie_func", "source": "skills/demo/SKILL.md", "line": 3, "def_files": ["scripts/lib/foo.py"]}
    ]


def test_print_advisory_clean(capsys):
    cli._print_skill_reachability_advisory({"applicable": True, "evaluated_count": 5, "unreachable": []})
    out = capsys.readouterr().out
    assert "✓" in out
    assert "該当なし" in out


def test_print_advisory_non_applicable(capsys):
    cli._print_skill_reachability_advisory({"applicable": False})
    out = capsys.readouterr().out
    assert "非該当" in out


def test_print_advisory_warns_with_evidence(capsys):
    cli._print_skill_reachability_advisory(
        {
            "applicable": True,
            "evaluated_count": 3,
            "unreachable": [
                {"name": "zombie_func", "source": "skills/demo/SKILL.md", "line": 3, "def_files": ["scripts/lib/foo.py"]}
            ],
        }
    )
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "zombie_func" in out
    assert "skills/demo/SKILL.md" in out


def test_light_layer_includes_advisory_without_affecting_exit_code(monkeypatch, tmp_path, capsys):
    """到達不能が検出されても light の exit code は変わらない（非ブロッキング, #191）。"""
    _patch_layers(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_skill_reachability_advisory", lambda repo_root: {
        "applicable": True,
        "evaluated_count": 3,
        "unreachable": [
            {"name": "zombie_func", "source": "skills/demo/SKILL.md", "line": 3, "def_files": ["scripts/lib/foo.py"]}
        ],
    })
    rc = cli.main(["--layer", "light", "--json", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skill_reachability"]["unreachable"][0]["name"] == "zombie_func"


def test_all_layer_prints_advisory_section(monkeypatch, tmp_path, capsys):
    _patch_layers(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_run_layer1", lambda repo_root, out_dir: {"checks": [], "result_path": None})
    monkeypatch.setattr(cli, "_run_skill_reachability_advisory", lambda repo_root: {
        "applicable": True, "evaluated_count": 1, "unreachable": [],
    })
    rc = cli.main(["--layer", "all", "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Advisory: Skill Declaration Reachability" in out
