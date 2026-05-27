"""skill_extractor — TrajectoryRecord をスキル別にグループ化し候補を生成する。

trajectory_sampler を呼び出し、スキル別にグループ化。
generalizability_score を計算してフィルタリングし、
skill-triage の missed_skills 形式に変換して返す。

LLM 呼び出し一切なし。

Issue #238 Phase 1
"""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from skill_extractor.trajectory_sampler import (
    TrajectoryRecord,
    sample_trajectories,
    DEFAULT_MAX_FILES,
)

# ── 定数 ──────────────────────────────────────────────────

DEFAULT_MIN_CLUSTER_SIZE = 2
"""スキル候補として返す最小クラスタサイズ。"""

SPECIALIZATION_PENALTY_THRESHOLD = 0.8
"""成功率がこの値を超え、かつクラスタが小さい場合に特化度ペナルティを適用。"""


# ── 公開関数 ──────────────────────────────────────────────


def extract_skill_candidates(
    projects_root: Optional[Path] = None,
    max_files: int = DEFAULT_MAX_FILES,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> List[Dict[str, Any]]:
    """セッション履歴からスキル候補を抽出して missed_skills 形式で返す。

    Args:
        projects_root: walk するルートディレクトリ。None の場合は
            ~/.claude/projects/ を使用する。
        max_files: サンプリングする最大ファイル数。
        min_cluster_size: 候補として返す最小クラスタサイズ。

    Returns:
        skill-triage の missed_skills 互換フォーマットのリスト::

            [
                {
                    "skill_name": "rl-anything:implement",
                    "session_count": 5,
                    "generalizability_score": 0.72,
                    "success_rate": 0.8,
                    "source": "codeskill_extraction",
                    "sample_prompts": ["実装して", "コードを書いて"],
                },
                ...
            ]
    """
    records = sample_trajectories(
        projects_root=projects_root,
        max_files=max_files,
    )

    grouped = _group_by_skill(records)

    candidates: List[Dict[str, Any]] = []
    for skill_name, skill_records in grouped.items():
        if len(skill_records) < min_cluster_size:
            continue

        score = _compute_generalizability_score(skill_records)
        success_rate = _compute_success_rate(skill_records)
        sample_prompts = _collect_sample_prompts(skill_records, max_samples=3)

        candidates.append(
            {
                "skill_name": skill_name,
                "session_count": len(skill_records),
                "generalizability_score": round(score, 4),
                "success_rate": round(success_rate, 4),
                "source": "codeskill_extraction",
                "sample_prompts": sample_prompts,
            }
        )

    # generalizability_score 降順でソート
    candidates.sort(key=lambda c: c["generalizability_score"], reverse=True)
    return candidates


# ── 内部関数 ──────────────────────────────────────────────


def _group_by_skill(
    records: List[TrajectoryRecord],
) -> Dict[str, List[TrajectoryRecord]]:
    """TrajectoryRecord をスキル名でグループ化する。

    Args:
        records: TrajectoryRecord のリスト。

    Returns:
        {skill_name: [TrajectoryRecord, ...]} の dict。
    """
    grouped: Dict[str, List[TrajectoryRecord]] = defaultdict(list)
    for rec in records:
        grouped[rec.skill_name].append(rec)
    return dict(grouped)


def _compute_generalizability_score(
    records: List[TrajectoryRecord],
    specialization_factor: float = 1.0,
) -> float:
    """generalizability_score を計算する。

    スコア = clamp(cluster_size_score * success_rate / specialization_factor, 0, 1)

    - cluster_size_score: log(N+1) / log(N_max+1) で正規化（N_max=50）
    - success_rate: success / total
    - specialization_factor: 特化度ペナルティ（高いほど汎用性が低い）

    Args:
        records: 同一スキルの TrajectoryRecord リスト。
        specialization_factor: 特化度ペナルティ（デフォルト 1.0 = ペナルティなし）。

    Returns:
        0.0 〜 1.0 のスコア。
    """
    if not records:
        return 0.0

    n = len(records)
    n_max = DEFAULT_MAX_FILES  # trajectory_sampler のサンプリング上限に合わせた正規化基準

    # クラスタサイズスコア: log スケールで正規化
    size_score = math.log(n + 1) / math.log(n_max + 1)
    size_score = min(1.0, size_score)

    # 成功率
    success_rate = _compute_success_rate(records)

    # 特化度ペナルティ適用
    sf = max(0.1, specialization_factor)  # ZeroDivision 防止
    raw_score = size_score * success_rate / sf

    return max(0.0, min(1.0, raw_score))


def _compute_success_rate(records: List[TrajectoryRecord]) -> float:
    """success / total を計算する。unknown は成功扱いとする。

    Args:
        records: TrajectoryRecord のリスト。

    Returns:
        0.0 〜 1.0 の成功率。records が空の場合は 0.0。
    """
    if not records:
        return 0.0

    total = len(records)
    # failure 以外 (success / unknown) は成功とみなす
    success = sum(1 for r in records if r.outcome != "failure")
    return success / total


def _collect_sample_prompts(
    records: List[TrajectoryRecord],
    max_samples: int = 3,
) -> List[str]:
    """空でない user_prompt を最大 max_samples 件収集する。

    Args:
        records: TrajectoryRecord のリスト。
        max_samples: 最大収集件数。

    Returns:
        user_prompt の文字列リスト（重複除去済み）。
    """
    seen: set = set()
    prompts: List[str] = []
    for rec in records:
        p = rec.user_prompt.strip()
        if p and p not in seen:
            prompts.append(p)
            seen.add(p)
        if len(prompts) >= max_samples:
            break
    return prompts
