"""memory_conflict observability builder のテスト（#83）。

``sections_contagion`` / ``sections_memory`` と同じ
``(project_dir) -> Optional[List[str]]`` 契約。advisory 表示のみ（スコア非関与）。
compute_conflicts を monkeypatch で差し替え、verdict → 行レンダリングと PJ スコープ
受け渡しのみを検証する（実ストア / 実 ~/.claude を読まない）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit import memory_conflict as mcf  # noqa: E402
from audit.sections_conflict import build_memory_conflict_section  # noqa: E402


def _pair(key="gstack"):
    return mcf.ConflictPair(
        key=key,
        pos_path=Path("use_gstack.md"),
        pos_value="新規開発は `gstack` フローで進める",
        neg_path=Path("avoid_gstack.md"),
        neg_value="`gstack` を使わない",
    )


def _report(**kw):
    base = dict(applicable=True, total_facts=5, conflicts=[])
    base.update(kw)
    return mcf.ConflictReport(**base)


def _patch(monkeypatch, report):
    monkeypatch.setattr(mcf, "compute_conflicts", lambda project_dir: report)


def test_none_when_not_applicable(tmp_path, monkeypatch):
    """applicable=False（floor 未満 / memory 無し）→ None（沈黙）。"""
    _patch(monkeypatch, _report(applicable=False, total_facts=2))
    assert build_memory_conflict_section(tmp_path) is None


def test_no_conflicts_shows_check_line(tmp_path, monkeypatch):
    """conflicts 空 → ✓ no conflicts (N facts scanned)。"""
    _patch(monkeypatch, _report(total_facts=7, conflicts=[]))
    section = build_memory_conflict_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "Memory Conflict" in combined
    assert "✓ no conflicts" in combined
    assert "7 facts scanned" in combined
    assert "⚠" not in combined


def test_conflict_shows_warning_and_evidence(tmp_path, monkeypatch):
    """conflicts あり → ⚠ + key + 肯定 / 否定 2 fact のパス・対立値。"""
    _patch(monkeypatch, _report(total_facts=6, conflicts=[_pair()]))
    section = build_memory_conflict_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "gstack" in combined
    # evidence: 2 fact のパス + 対立値
    assert "use_gstack.md" in combined
    assert "avoid_gstack.md" in combined
    assert "使わない" in combined
    assert "recall" in combined  # 汚染リスクの誘導


def test_advisory_only_returns_str_list(tmp_path, monkeypatch):
    """builder は str の行リストのみ返す（advisory）。"""
    _patch(monkeypatch, _report(conflicts=[_pair()]))
    section = build_memory_conflict_section(tmp_path)
    assert isinstance(section, list)
    assert all(isinstance(line, str) for line in section)


def test_passes_project_dir_to_compute(tmp_path, monkeypatch):
    """builder は受け取った project_dir をそのまま compute_conflicts に渡す（PJ スコープ）。"""
    seen = {}

    def _fake(project_dir):
        seen["pd"] = project_dir
        return _report(applicable=False)

    monkeypatch.setattr(mcf, "compute_conflicts", _fake)
    build_memory_conflict_section(tmp_path / "mine")
    assert seen["pd"] == tmp_path / "mine"
