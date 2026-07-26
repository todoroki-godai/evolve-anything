"""Audit advisory を decision lane へ渡すための純粋な proposal adapter（#267）。

observability の section builder は表示専用であり、副作用を持たせない。本モジュールは
detector の構造化結果を ``AdvisoryProposal`` に変換する独立レイヤーを提供する。
収集・変換のみを行い、永続化や accept/reject 判定は行わない。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class AdvisoryProposal:
    """Decision lane に接続可能な advisory 提案の正準形。"""

    id: str
    detector_id: str
    title: str
    summary: str
    action: str
    target_paths: Tuple[str, ...]
    evidence: Mapping[str, Any]
    proposal_type: str = "advisory"

    def to_dict(self) -> Dict[str, Any]:
        """JSON 化可能な辞書へ変換する。"""
        data = asdict(self)
        data["target_paths"] = list(self.target_paths)
        data["evidence"] = dict(self.evidence)
        return data


Adapter = Callable[[Path], List[AdvisoryProposal]]


def _proposal_id(
    detector_id: str, target_paths: Tuple[str, ...], evidence: Mapping[str, Any]
) -> str:
    """同一 detector・対象・根拠に対して安定した content identity を返す。"""
    canonical = json.dumps(
        {
            "detector_id": detector_id,
            "target_paths": list(target_paths),
            "evidence": evidence,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "adv_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _relative_path(path: Path, project_dir: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _testpaths_coverage(project_dir: Path) -> List[AdvisoryProposal]:
    from testpaths_coverage import detect_uncovered_test_dirs

    report = detect_uncovered_test_dirs(project_dir)
    if not report.has_testpaths or not report.uncovered:
        return []

    targets = ("pytest.ini",)
    evidence = {
        "declared_testpaths": list(report.testpaths),
        "uncovered_test_dirs": list(report.uncovered),
    }
    return [
        AdvisoryProposal(
            id=_proposal_id("testpaths_coverage", targets, evidence),
            detector_id="testpaths_coverage",
            title="pytest testpaths の収集漏れを修正",
            summary=f"{len(report.uncovered)} 個の tests/ ディレクトリが収集対象外です。",
            action="pytest.ini の testpaths に未収集ディレクトリを追加する",
            target_paths=targets,
            evidence=evidence,
        )
    ]


def _invalid_frontmatter(project_dir: Path) -> List[AdvisoryProposal]:
    from audit.sections_invalid_frontmatter import detect_invalid_frontmatter

    proposals: List[AdvisoryProposal] = []
    for item in detect_invalid_frontmatter(project_dir):
        target = _relative_path(Path(item["skill_path"]), project_dir)
        targets = (target,)
        evidence = {
            "skill_name": item["skill_name"],
            "error": item["error"],
        }
        proposals.append(
            AdvisoryProposal(
                id=_proposal_id("invalid_frontmatter", targets, evidence),
                detector_id="invalid_frontmatter",
                title=f"{item['skill_name']} の frontmatter を修正",
                summary="YAML が不正なため、このスキルは自動発火できません。",
                action=f"{target} の YAML frontmatter を修正する",
                target_paths=targets,
                evidence=evidence,
            )
        )
    return proposals


ADVISORY_PROPOSAL_ADAPTERS: Dict[str, Adapter] = {
    "invalid_frontmatter": _invalid_frontmatter,
    "testpaths_coverage": _testpaths_coverage,
}


def collect_advisory_proposals(
    project_dir: Path,
    *,
    detector_ids: Optional[List[str]] = None,
) -> List[AdvisoryProposal]:
    """選択した detector の proposal を決定論順で返す。書き込みは行わない。"""
    project_dir = Path(project_dir)
    selected = (
        sorted(ADVISORY_PROPOSAL_ADAPTERS)
        if detector_ids is None
        else sorted(set(detector_ids))
    )
    unknown = [name for name in selected if name not in ADVISORY_PROPOSAL_ADAPTERS]
    if unknown:
        raise ValueError(f"unknown advisory proposal detector: {', '.join(unknown)}")

    proposals: List[AdvisoryProposal] = []
    for detector_id in selected:
        proposals.extend(ADVISORY_PROPOSAL_ADAPTERS[detector_id](project_dir))
    return sorted(proposals, key=lambda proposal: (proposal.detector_id, proposal.id))
