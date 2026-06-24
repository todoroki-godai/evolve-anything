"""memory_contagion observability builder のテスト（#73）。

`sections_skill_vuln` / `sections_capture` と同じ
`(project_dir) -> Optional[List[str]]` 契約。advisory 表示のみ（スコア非関与）。
決定論・LLM 非依存。compute_contagion を monkeypatch で差し替え、builder の
verdict → 行レンダリングと PJ スコープ受け渡しのみを検証する。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit import memory_contagion as mc  # noqa: E402
from audit.sections_contagion import build_memory_contagion_section  # noqa: E402


def _report(**kw):
    base = dict(
        applicable=True,
        human_total=0,
        machine_total=0,
        human_corrections=0,
        machine_corrections=0,
        confirmed_idioms=0,
        unconfirmed_idioms=0,
        verdict="healthy",
    )
    base.update(kw)
    return mc.ContagionReport(**base)


def _patch(monkeypatch, report):
    monkeypatch.setattr(mc, "compute_contagion", lambda project_dir: report)


def test_none_when_not_applicable(tmp_path, monkeypatch):
    """applicable=False（評価データ無し）→ None（沈黙）。"""
    _patch(monkeypatch, _report(applicable=False, verdict="healthy"))
    assert build_memory_contagion_section(tmp_path) is None


def test_healthy_shows_check_line(tmp_path, monkeypatch):
    """healthy → ✓ 1行 + 内訳1行。"""
    _patch(monkeypatch, _report(
        verdict="healthy", human_total=6, machine_total=12,
        human_corrections=6, machine_corrections=12,
    ))
    section = build_memory_contagion_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "Memory Contagion" in combined
    assert "✓" in combined
    assert "human=6" in combined and "machine=12" in combined


def test_no_human_baseline_shows_info_line(tmp_path, monkeypatch):
    """no_human_baseline → ℹ + 内訳 + 基準が立たない旨の誘導（⚠ ではない）。"""
    _patch(monkeypatch, _report(
        verdict="no_human_baseline", human_total=0, machine_total=15,
        machine_corrections=15,
    ))
    section = build_memory_contagion_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "ℹ" in combined
    assert "⚠" not in combined  # cry wolf しない
    assert "human=0" in combined and "machine=15" in combined
    assert "reflect" in combined or "daily" in combined


def test_contagion_risk_shows_warning_and_evidence(tmp_path, monkeypatch):
    """contagion_risk → ⚠ + evidence（corrections/idioms 別の内訳）。"""
    _patch(monkeypatch, _report(
        verdict="contagion_risk",
        human_total=4, machine_total=12,
        human_corrections=4, machine_corrections=12,
        confirmed_idioms=0, unconfirmed_idioms=0,
    ))
    section = build_memory_contagion_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "authority bias" in combined or "authority" in combined
    # evidence: corrections と idioms 別の数が出る
    assert "corrections" in combined
    assert "idiom" in combined


def test_advisory_only_returns_str_list(tmp_path, monkeypatch):
    """builder は str の行リストのみ返す（advisory）。"""
    _patch(monkeypatch, _report(verdict="healthy", human_total=1, machine_total=1))
    section = build_memory_contagion_section(tmp_path)
    assert isinstance(section, list)
    assert all(isinstance(line, str) for line in section)


def test_passes_project_dir_to_compute(tmp_path, monkeypatch):
    """builder は受け取った project_dir をそのまま compute_contagion に渡す（PJ スコープ）。"""
    seen = {}

    def _fake(project_dir):
        seen["pd"] = project_dir
        return _report(applicable=False)

    monkeypatch.setattr(mc, "compute_contagion", _fake)
    build_memory_contagion_section(tmp_path / "mine")
    assert seen["pd"] == tmp_path / "mine"
