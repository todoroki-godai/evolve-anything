"""skill_extractor — LLM なしでセッション履歴からスキル候補を自動抽出するパッケージ。

Phase 1: trajectory_sampler + skill_extractor
  - trajectory_sampler: ~/.claude/projects/ 配下の raw sessions を walk し、
    <command-name> タグを持つターンから TrajectoryRecord を抽出する
  - skill_extractor: TrajectoryRecord をスキル別にグループ化し、
    generalizability_score を計算して skill-triage の missed_skills 形式に変換する

Issue #238 Phase 1
"""
from skill_extractor.trajectory_sampler import (
    TrajectoryRecord,
    sample_trajectories,
)
from skill_extractor.skill_extractor import extract_skill_candidates
from skill_extractor.effectiveness import (
    compute_effectiveness,
    compute_diversity,
    compute_recurrence,
    compute_contrast,
    effectiveness_multiplier,
)

__all__ = [
    "TrajectoryRecord",
    "sample_trajectories",
    "extract_skill_candidates",
    "compute_effectiveness",
    "compute_diversity",
    "compute_recurrence",
    "compute_contrast",
    "effectiveness_multiplier",
]
