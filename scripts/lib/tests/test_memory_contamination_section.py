"""memory_contamination observability builder のテスト（#108）。

`sections_contagion` と同じ `(project_dir) -> Optional[List[str]]` 契約。advisory 表示のみ
（スコア非関与）。決定論・LLM 非依存。scan_memory_dir / _resolve_memory_dir を monkeypatch し、
report → 行レンダリングと PJ スコープ受け渡しのみを検証する。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import memory_capability  # noqa: E402
import memory_guard as mg  # noqa: E402
from audit.sections_memory import build_memory_contamination_section  # noqa: E402


def _hit(filename="bad.md", category="prompt_injection"):
    return mg.ContaminationHit(
        category=category,
        severity="MEDIUM",
        pattern_id="prompt_injection.ignore_previous",
        snippet="ignore previous instructions",
        line=3,
        filename=filename,
    )


def _patch(monkeypatch, report):
    monkeypatch.setattr(mg, "scan_memory_dir", lambda md: report)
    monkeypatch.setattr(memory_capability, "_resolve_memory_dir", lambda p: Path("/x"))


def test_none_when_no_findings(tmp_path, monkeypatch):
    """走査したが汚染なし → None（沈黙・「無ければ非表示」）。"""
    _patch(monkeypatch, mg.MemoryContaminationReport(applicable=True, scanned_files=2, hits=[]))
    assert build_memory_contamination_section(tmp_path) is None


def test_none_when_not_applicable(tmp_path, monkeypatch):
    """memory dir 不在（applicable=False）→ None（沈黙）。"""
    _patch(monkeypatch, mg.MemoryContaminationReport(applicable=False))
    assert build_memory_contamination_section(tmp_path) is None


def test_warns_with_evidence(tmp_path, monkeypatch):
    """汚染あり → ⚠ + ファイル名 / カテゴリ / snippet を evidence 表示。"""
    _patch(monkeypatch, mg.MemoryContaminationReport(applicable=True, scanned_files=3, hits=[_hit()]))
    section = build_memory_contamination_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert section[0].startswith("## ")
    assert "Memory Contamination" in combined
    assert "⚠" in combined
    assert "bad.md" in combined
    assert "prompt_injection" in combined
    assert section[-1] == ""  # header/trailer 規約


def test_advisory_only_returns_str_list(tmp_path, monkeypatch):
    _patch(monkeypatch, mg.MemoryContaminationReport(applicable=True, scanned_files=1, hits=[_hit()]))
    section = build_memory_contamination_section(tmp_path)
    assert isinstance(section, list)
    assert all(isinstance(line, str) for line in section)


def test_passes_project_dir_to_resolve(tmp_path, monkeypatch):
    """builder は受け取った project_dir をそのまま memory dir 解決へ渡す（PJ スコープ）。"""
    seen = {}

    def _fake_resolve(p):
        seen["p"] = p
        return Path("/x")

    monkeypatch.setattr(memory_capability, "_resolve_memory_dir", _fake_resolve)
    monkeypatch.setattr(mg, "scan_memory_dir", lambda md: mg.MemoryContaminationReport(applicable=False))
    build_memory_contamination_section(tmp_path / "mine")
    assert seen["p"] == tmp_path / "mine"
