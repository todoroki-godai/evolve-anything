"""issues_summary — audit が growth-state cache に書き出す issue カウント。

fleet status が複数 PJ の問題を一覧化するため、audit run のついでに
counts のみを収集する。fleet MVP-D (#22) で導入。

詳細:
- 5 種のカウントのみ保持し、診断データの所在は audit レポート/scripts に残す
- corrections は `reflect_status == "applied"` を「処理済」として除外
- skill_quality は quality_baselines の moving avg vs baseline で degraded 判定
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


@dataclass
class IssuesSummary:
    """audit が検出した issue のカウント（counts only）。"""

    line_violations: int = 0
    hardcoded_values: int = 0
    potential_duplicates: int = 0
    corrections_unprocessed: int = 0
    skill_quality_degraded_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


_DEGRADATION_THRESHOLD = 0.20  # audit.DEGRADATION_THRESHOLD と同じ既定値


def _count_unprocessed_corrections(corrections: Iterable[Mapping[str, Any]] | None) -> int:
    """`reflect_status == "applied"` 以外を unprocessed として数える。"""
    if not corrections:
        return 0
    n = 0
    for rec in corrections:
        if not isinstance(rec, Mapping):
            continue
        if rec.get("reflect_status") != "applied":
            n += 1
    return n


def _count_degraded_skills(
    quality_baselines: Iterable[Mapping[str, Any]] | None,
    *,
    threshold: float = _DEGRADATION_THRESHOLD,
) -> int:
    """quality_baselines (records list) から degraded スキル数を数える。

    判定ロジックは audit.build_quality_trends_section と同じ:
    1スキル毎に baseline (initial avg) と moving avg を比較し、
    decline_rate >= threshold なら degraded。記録 2 件未満はスキップ。
    """
    if not quality_baselines:
        return 0

    by_skill: dict[str, list[Mapping[str, Any]]] = {}
    for rec in quality_baselines:
        if not isinstance(rec, Mapping):
            continue
        name = rec.get("skill_name") or rec.get("name")
        if not isinstance(name, str):
            continue
        by_skill.setdefault(name, []).append(rec)

    degraded = 0
    for recs in by_skill.values():
        if len(recs) < 2:
            continue
        scores = [r.get("score") for r in recs if isinstance(r.get("score"), (int, float))]
        if len(scores) < 2:
            continue
        # baseline = 最初の半分 (or 最初 1 件)、moving avg = 後半（audit 実装と整合）
        half = max(1, len(scores) // 2)
        baseline = sum(scores[:half]) / half
        avg = sum(scores[half:]) / max(1, len(scores) - half)
        if baseline <= 0:
            continue
        decline = (baseline - avg) / baseline
        if decline >= threshold:
            degraded += 1
    return degraded


def compute_issues_summary(
    *,
    violations: Iterable[Mapping[str, Any]] | None = None,
    hardcoded_values: Iterable[Mapping[str, Any]] | None = None,
    duplicates: Iterable[Mapping[str, Any]] | None = None,
    corrections: Iterable[Mapping[str, Any]] | None = None,
    quality_baselines: Iterable[Mapping[str, Any]] | None = None,
) -> IssuesSummary:
    """audit の中間データから IssuesSummary を組み立てる。

    全引数は None / 空配列許容（その場合は対応カウントが 0）。
    audit.py から呼び出され、結果は growth-state cache の `issues_summary` に
    入って fleet status から参照される。
    """
    def _safe_len(it: Iterable[Any] | None) -> int:
        if it is None:
            return 0
        try:
            return sum(1 for _ in it)
        except TypeError:
            return 0

    return IssuesSummary(
        line_violations=_safe_len(violations),
        hardcoded_values=_safe_len(hardcoded_values),
        potential_duplicates=_safe_len(duplicates),
        corrections_unprocessed=_count_unprocessed_corrections(corrections),
        skill_quality_degraded_count=_count_degraded_skills(quality_baselines),
    )
