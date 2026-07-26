"""advisory proposal adapter の契約テスト（#267）。"""
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

from advisory_proposals import collect_advisory_proposals  # noqa: E402


def _write_invalid_skill(root: Path, name: str = "broken") -> Path:
    skill = root / ".claude" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: broken\ndescription: トリガー: 壊れている\n---\n# Broken\n",
        encoding="utf-8",
    )
    return skill


def test_invalid_frontmatter_is_one_proposal_per_skill(tmp_path):
    _write_invalid_skill(tmp_path)

    proposals = collect_advisory_proposals(
        tmp_path, detector_ids=["invalid_frontmatter"]
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.detector_id == "invalid_frontmatter"
    assert proposal.proposal_type == "advisory"
    assert proposal.target_paths == (".claude/skills/broken/SKILL.md",)
    assert proposal.evidence["skill_name"] == "broken"
    assert proposal.id.startswith("adv_")
    assert proposal.to_dict()["target_paths"] == [
        ".claude/skills/broken/SKILL.md"
    ]


def test_testpaths_coverage_is_structured_proposal(tmp_path):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n", encoding="utf-8"
    )
    covered = tmp_path / "tests"
    covered.mkdir()
    (covered / "test_ok.py").write_text("", encoding="utf-8")
    uncovered = tmp_path / "pkg" / "tests"
    uncovered.mkdir(parents=True)
    (uncovered / "test_missed.py").write_text("", encoding="utf-8")

    proposals = collect_advisory_proposals(
        tmp_path, detector_ids=["testpaths_coverage"]
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.detector_id == "testpaths_coverage"
    assert proposal.target_paths == ("pytest.ini",)
    assert proposal.evidence == {
        "declared_testpaths": ["tests"],
        "uncovered_test_dirs": ["pkg/tests"],
    }


def test_collection_is_deterministic_and_does_not_write(tmp_path):
    skill = _write_invalid_skill(tmp_path)
    before = skill.read_bytes()

    first = [
        proposal.to_dict()
        for proposal in collect_advisory_proposals(
            tmp_path, detector_ids=["invalid_frontmatter"]
        )
    ]
    second = [
        proposal.to_dict()
        for proposal in collect_advisory_proposals(
            tmp_path, detector_ids=["invalid_frontmatter"]
        )
    ]

    assert first == second
    assert skill.read_bytes() == before


def test_clean_detectors_return_no_proposals(tmp_path):
    assert collect_advisory_proposals(tmp_path) == []


def test_unknown_detector_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown advisory proposal detector"):
        collect_advisory_proposals(tmp_path, detector_ids=["not_registered"])
