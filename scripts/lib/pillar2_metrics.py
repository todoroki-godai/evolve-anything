"""柱2「照合済み反映」の producer 集計。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from reflect_fold import _parse_iso8601_utc, fold_corrections


PILLAR2_NOT_MEASURED_TARGETS = {
    "hook": {"reason": "no_store", "label": "記録ストアなし"},
    "pitfall_memory": {"reason": "mtime_collision", "label": "mtime 衝突"},
}

# 新方式は PR #596（merge 9882eaef / committer time 2026-09-01T09:29:07Z）で
# 導入された。この時刻は来歴の記録であって判定には使わない。
# 4件のうち3件は project_path が updater-index の基底で、evolve-anything 視点では
# global-looking として通過している。
# 追記時は対象 ID・基底の PJ・状態・柱2イベント不在・発見日時・承認根拠を人間が
# 確認し、通常のコードレビューを経ること。時刻のみを根拠に追記しない。
PRE_SCHEME_APPLIED_BASELINE = frozenset(
    {
        (
            "0f94d4a14da5472c93010b644f6ce46b",
            "5b3ca1f6eb6261647670a38e2dfc2fbc6a5e911dcf1e39a0ed0f30d8f9972a3e",
        ),
        (
            "411114e30ec74a1aacf14a1c0572daff",
            "7ac56098fa58b826f7afd4f98d1ae4683329d4f8d6bdb5103ebb70b3cfc5739f",
        ),
        (
            "74f0215b71b847a388f3a5af55e24b22",
            "40bea99ed326ef9a21ec7b2ee43a810ead9187186a7c7e66a8065f1a03408143",
        ),
        (
            "c25c83983e1f4a0a98b11133a02cab66",
            "908e9ee2e3bc5a4ba63e08df47d8144bce8548d9b7a5ba18d09e8a010774fca9",
        ),
    }
)


def _baseline_row_sha256(base: dict) -> str:
    # 基底行の全フィールド（柱2フィールドを含む）を key-sort・compact JSON に正規化して hash する。
    canonical = json.dumps(
        base, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _not_measured_targets() -> dict[str, dict[str, str]]:
    """公開定義から board の not_measured schema を生成する。"""
    return {
        target: {"reason": details["reason"]}
        for target, details in PILLAR2_NOT_MEASURED_TARGETS.items()
    }


def _snapshot_stat(path: Path) -> Optional[tuple[int, int]]:
    """path の比較可能な状態を返す。存在しない状態も None として比較する。"""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _read_stable_snapshot(
    corrections_path: Path,
    events_path: Path,
    *,
    max_retries: int = 3,
):
    """base/event を read 前後の stat が一致するまで有限回読み直す。"""
    from fleet.queue_materials import read_corrections_records_with_health

    for _ in range(max_retries):
        stat_before = (
            _snapshot_stat(corrections_path),
            _snapshot_stat(events_path),
        )
        base_records, base_health = read_corrections_records_with_health(corrections_path)
        event_records, event_health = read_corrections_records_with_health(events_path)
        stat_after = (
            _snapshot_stat(corrections_path),
            _snapshot_stat(events_path),
        )
        if stat_before == stat_after:
            return base_records, base_health, event_records, event_health, True
    return base_records, base_health, event_records, event_health, False


def _classify_project_scope(correction: dict, project_root: Path) -> str:
    """実在する reflect 実装の scope 判定を共有する。"""
    try:
        from reflect import classify_project_scope
    except ModuleNotFoundError:
        reflect_path = (
            Path(__file__).resolve().parents[2]
            / "skills"
            / "reflect"
            / "scripts"
            / "reflect.py"
        )
        spec = importlib.util.spec_from_file_location("_pillar2_reflect", reflect_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"reflect module cannot be loaded: {reflect_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        classify_project_scope = module.classify_project_scope
    return classify_project_scope(correction, str(project_root))


def _pillar2_project_scope(correction: dict, project_root: Path) -> str:
    """slug 表現を吸収してから既存の project scope 判定へ委譲する。"""
    from pj_slug import pj_slug_aliases_for, resolve_pj_slug
    from rl_common.persistence import project_name_from_dir

    project_path = correction.get("project_path")
    if isinstance(project_path, str) and project_path:
        matching_slugs = pj_slug_aliases_for(resolve_pj_slug(project_root))
        matching_slugs.update(
            pj_slug_aliases_for(project_name_from_dir(str(project_root)))
        )
        if project_path in matching_slugs:
            return "same-project"
    return _classify_project_scope(correction, project_root)


def _count_scoped_invalid_base_ids(fold_health, project_root: Path) -> dict[str, int]:
    """不正 ID 基底を柱2の信頼境界順に分類する。"""
    counts = {
        "invalid_base_id_applied_same_project_row_count": 0,
        "invalid_base_id_applied_global_looking_row_count": 0,
        "invalid_base_id_non_applied_same_project_row_count": 0,
        "invalid_base_id_non_applied_global_looking_row_count": 0,
    }
    for correction in fold_health.invalid_base_id_records:
        scope = _pillar2_project_scope(correction, project_root)
        if scope not in ("same-project", "global-looking"):
            continue
        if correction.get("invalidated"):
            continue
        status = (
            "applied"
            if correction.get("reflect_status") == "applied"
            else "non_applied"
        )
        scope_key = scope.replace("-", "_")
        counts[f"invalid_base_id_{status}_{scope_key}_row_count"] += 1
    return counts


def count_applied_reflections(
    project_root: Path,
    *,
    corrections_path: Optional[Path] = None,
    events_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    window_days: int = 30,
) -> dict:
    """対象期間に照合できた反映を数える。

    反映先の存在は記録時点の事実であり、その後の削除・変更は追跡しない。
    ``measured=False`` の場合、``count`` は全実反映件数でなく照合できた部分集合である。
    """
    from prune.config import DEFAULT_DECAY_DAYS
    import rl_common

    corrections_path = corrections_path or (rl_common.DATA_DIR / "corrections.jsonl")
    events_path = events_path or (rl_common.DATA_DIR / "reflect_apply_events.jsonl")
    (
        base_records,
        base_health,
        event_records,
        event_health,
        snapshot_stable,
    ) = _read_stable_snapshot(corrections_path, events_path)

    now = now or datetime.now(timezone.utc)
    folded, fold_health = fold_corrections(
        base_records,
        event_records,
        now=now,
        decay_grace_days=DEFAULT_DECAY_DAYS,
    )
    window_start = now - timedelta(days=window_days)
    invalid_base_id_counts = _count_scoped_invalid_base_ids(
        fold_health, Path(project_root)
    )
    invalid_base_id_applied_row_count = (
        invalid_base_id_counts["invalid_base_id_applied_same_project_row_count"]
        + invalid_base_id_counts["invalid_base_id_applied_global_looking_row_count"]
    )
    invalid_base_id_non_applied_row_count = (
        invalid_base_id_counts["invalid_base_id_non_applied_same_project_row_count"]
        + invalid_base_id_counts[
            "invalid_base_id_non_applied_global_looking_row_count"
        ]
    )

    eligible = []
    legacy_unverified_count = 0
    pre_scheme_excluded_count = 0
    invalidated_count = 0
    other_kind_count = 0
    for folded_correction in folded:
        scope = _pillar2_project_scope(folded_correction.base, Path(project_root))
        if scope not in ("same-project", "global-looking"):
            continue
        if folded_correction.base.get("invalidated"):
            invalidated_count += 1
            continue
        if folded_correction.base.get("reflect_status") != "applied":
            continue
        if not folded_correction.has_pillar2_fields:
            if (
                (
                    folded_correction.base.get("correction_id"),
                    _baseline_row_sha256(folded_correction.base),
                )
                in PRE_SCHEME_APPLIED_BASELINE
            ):
                pre_scheme_excluded_count += 1
            else:
                legacy_unverified_count += 1
            continue
        if folded_correction.reflect_target_kind == "other":
            other_kind_count += 1
            continue
        timestamp = _parse_iso8601_utc(folded_correction.reflect_applied_at)
        if timestamp is None or not (window_start <= timestamp <= now):
            continue
        eligible.append(folded_correction)

    groups: dict[tuple, list] = {}
    for folded_correction in eligible:
        key = (
            folded_correction.reflect_target_kind,
            folded_correction.reflect_target_path,
            folded_correction.reflect_draft_line.strip(),
        )
        groups.setdefault(key, []).append(folded_correction)

    applied_list = [
        {
            "target_kind": key[0],
            "target_path": key[1],
            "reflect_applied_at": min(
                item.reflect_applied_at for item in grouped_corrections
            ),
            "reconciled": any(item.reconciled for item in grouped_corrections),
        }
        for key, grouped_corrections in groups.items()
    ][:10]

    degraded = (
        not snapshot_stable
        or not base_health["readable"]
        or not event_health["readable"]
        or base_health["malformed_lines"] > 0
        or event_health["malformed_lines"] > 0
        or fold_health.orphan_events_unexpected > 0
        or fold_health.unknown_schema_events > 0
        or fold_health.invalid_events > 0
        or fold_health.duplicate_base_row_count > 0
        or fold_health.duplicate_event_row_count > 0
        or fold_health.orphan_confirmations > 0
        or fold_health.duplicate_confirmations > 0
        or fold_health.hash_mismatch_count > 0
        or legacy_unverified_count > 0
        or invalid_base_id_applied_row_count > 0
    )

    return {
        "count": len(groups),
        "measured": not degraded,
        "legacy_unverified_count": legacy_unverified_count,
        "pre_scheme_excluded_count": pre_scheme_excluded_count,
        "invalidated_count": invalidated_count,
        "other_kind_count": other_kind_count,
        "reconciled_count": sum(1 for item in eligible if item.reconciled),
        "applied_list": applied_list,
        "health": {
            "degraded": degraded,
            "snapshot_stable": snapshot_stable,
            "base_readable": base_health["readable"],
            "base_read_error": base_health["error"],
            "base_malformed_lines": base_health["malformed_lines"],
            "events_readable": event_health["readable"],
            "events_read_error": event_health["error"],
            "events_malformed_lines": event_health["malformed_lines"],
            "orphan_events_unexpected": fold_health.orphan_events_unexpected,
            "orphan_events_expected": fold_health.orphan_events_expected,
            "unknown_schema_events": fold_health.unknown_schema_events,
            "invalid_events": fold_health.invalid_events,
            "duplicate_base_row_count": fold_health.duplicate_base_row_count,
            "duplicate_event_row_count": fold_health.duplicate_event_row_count,
            "orphan_confirmations": fold_health.orphan_confirmations,
            "duplicate_confirmations": fold_health.duplicate_confirmations,
            "hash_mismatch_count": fold_health.hash_mismatch_count,
            "invalid_base_id_applied_row_count": invalid_base_id_applied_row_count,
            "invalid_base_id_non_applied_row_count": invalid_base_id_non_applied_row_count,
            **invalid_base_id_counts,
        },
        "not_measured": _not_measured_targets(),
        "generated_at": now.isoformat(),
    }
