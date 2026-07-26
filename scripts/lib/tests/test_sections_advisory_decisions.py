"""Advisory Decisions observability section のテスト（#284）。

記録0件のPJでは沈黙する（observability を空に保つ既存契約）ことと、
detector 別 accept/reject テーブル・accept 0 件 detector の ⚠ を byte で固定する。
"""
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import advisory_decision_log as adl  # noqa: E402
import rl_common  # noqa: E402
from audit.sections_advisory_decisions import (  # noqa: E402
    build_advisory_decisions_section,
)
from evolve_decisions import resolve_slug  # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(rl_common, "DATA_DIR", data_dir)
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return data_dir


def _record(slug, detector_id, decision, proposal_id):
    adl.record_advisory_decision(
        slug=slug,
        proposal_id=proposal_id,
        detector_id=detector_id,
        target_path="pytest.ini",
        decision=decision,
    )


def test_section_is_silent_when_no_records(isolated, tmp_path):
    assert build_advisory_decisions_section(tmp_path) is None


def test_section_renders_per_detector_table(isolated, tmp_path):
    slug = resolve_slug(tmp_path)
    _record(slug, "invalid_frontmatter", "accept", "adv_1")
    _record(slug, "invalid_frontmatter", "accept", "adv_2")
    _record(slug, "invalid_frontmatter", "reject", "adv_3")

    lines = build_advisory_decisions_section(tmp_path)

    assert "| invalid_frontmatter | 2 | 1 | 67% |" in lines
    assert not any(line.startswith("⚠") for line in lines)


def test_section_flags_detector_with_zero_accepts(isolated, tmp_path):
    slug = resolve_slug(tmp_path)
    _record(slug, "testpaths_coverage", "reject", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert any("⚠ accept 0 件の detector: testpaths_coverage" in line for line in lines)


def test_section_ignores_other_projects(isolated, tmp_path):
    _record("some-other-pj", "invalid_frontmatter", "accept", "adv_1")

    assert build_advisory_decisions_section(tmp_path) is None


def test_section_renders_header_and_trailer(isolated, tmp_path):
    _record(resolve_slug(tmp_path), "invalid_frontmatter", "accept", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert lines[0] == "## Advisory Decisions"
    assert lines[-1] == ""
