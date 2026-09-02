import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import pillar2_metrics as metrics
from reflect_fold import _hash_correction_message


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
BASE_ID = "a" * 32
ATTEMPT_ID = "b" * 32
APPLIED_ID = "c" * 32
BASELINE_IDS = {
    "411114e30ec74a1aacf14a1c0572daff",
    "c25c83983e1f4a0a98b11133a02cab66",
    "74f0215b71b847a388f3a5af55e24b22",
    "0f94d4a14da5472c93010b644f6ce46b",
}
BASELINE_FINGERPRINTS = {
    ("0f94d4a14da5472c93010b644f6ce46b", "5b3ca1f6eb6261647670a38e2dfc2fbc6a5e911dcf1e39a0ed0f30d8f9972a3e"),
    ("411114e30ec74a1aacf14a1c0572daff", "7ac56098fa58b826f7afd4f98d1ae4683329d4f8d6bdb5103ebb70b3cfc5739f"),
    ("74f0215b71b847a388f3a5af55e24b22", "40bea99ed326ef9a21ec7b2ee43a810ead9187186a7c7e66a8065f1a03408143"),
    ("c25c83983e1f4a0a98b11133a02cab66", "908e9ee2e3bc5a4ba63e08df47d8144bce8548d9b7a5ba18d09e8a010774fca9"),
}
POST_SCHEME_APPLIED_ID = "6aa192618b1043c3a8afe19ecab18c85"


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _base(**overrides):
    value = {
        "correction_id": BASE_ID,
        "extracted_learning": "Use the stable API",
        "reflect_status": "applied",
        "project_path": None,
        "timestamp": "2026-08-31T00:00:00+00:00",
    }
    value.update(overrides)
    return value


def _events(applied_at="2026-08-31T10:01:00+00:00"):
    base = _base()
    return [
        {
            "correction_id": ATTEMPT_ID, "schema_version": 1,
            "event_type": "correction_apply_attempted", "target_correction_id": BASE_ID,
            "reflect_target_kind": "project_rule", "reflect_target_path": "repo:.claude/rules/a.md",
            "reflect_draft_line": "Use the stable API",
            "correction_message_sha256": _hash_correction_message(base),
            "attempted_at": "2026-08-31T10:00:00+00:00",
        },
        {
            "correction_id": APPLIED_ID, "schema_version": 1,
            "event_type": "correction_applied", "target_correction_id": BASE_ID,
            "confirms_attempt_id": ATTEMPT_ID, "reflect_applied_at": applied_at,
        },
    ]


def _count(tmp_path, bases, events):
    corrections = tmp_path / "corrections.jsonl"
    event_path = tmp_path / "reflect_apply_events.jsonl"
    _write(corrections, bases)
    _write(event_path, events)
    return metrics.count_applied_reflections(
        tmp_path, corrections_path=corrections, events_path=event_path, now=NOW
    )


def _install_test_baseline(monkeypatch, bases):
    fingerprints = frozenset(
        (base["correction_id"], metrics._baseline_row_sha256(base)) for base in bases
    )
    monkeypatch.setattr(metrics, "PRE_SCHEME_APPLIED_BASELINE", fingerprints)


def test_count_applied_reflections_uses_reflect_applied_at(tmp_path):
    old = _count(tmp_path, [_base(timestamp="2026-08-31T00:00:00+00:00")], _events("2026-07-01T00:00:00+00:00"))
    fresh = _count(tmp_path, [_base(timestamp="2026-01-01T00:00:00+00:00")], _events())
    assert old["count"] == 0
    assert fresh["count"] == 1
    assert fresh["applied_list"][0]["reflect_applied_at"] == "2026-08-31T10:01:00+00:00"


def test_invalidated_excluded_from_count(tmp_path):
    result = _count(tmp_path, [_base(invalidated=True)], _events())
    assert result["count"] == 0
    assert result["invalidated_count"] == 1


def test_pending_backlog_does_not_pollute_legacy_unverified(tmp_path):
    result = _count(tmp_path, [_base(reflect_status="pending")], [])
    assert result["legacy_unverified_count"] == 0
    assert result["measured"] is True


def test_unstable_snapshot_forces_not_measured(tmp_path):
    healthy = {"readable": True, "error": None, "malformed_lines": 0}
    reads = [([_base()], healthy), (_events(), healthy)] * 3
    with mock.patch.object(metrics, "_snapshot_stat", side_effect=range(12)), mock.patch(
        "fleet.queue_materials.read_corrections_records_with_health", side_effect=reads
    ):
        result = metrics.count_applied_reflections(
            tmp_path,
            corrections_path=tmp_path / "corrections.jsonl",
            events_path=tmp_path / "events.jsonl",
            now=NOW,
        )
    assert result["health"]["snapshot_stable"] is False
    assert result["measured"] is False


def test_sibling_worktree_authoritative_slug_is_same_project(monkeypatch, tmp_path):
    monkeypatch.setattr("pj_slug.resolve_pj_slug", lambda root: "evolve-anything")
    monkeypatch.setattr("rl_common.persistence.project_name_from_dir", lambda root: "ea-587")
    correction = {"project_path": "evolve-anything", "message": "project detail"}
    assert metrics._pillar2_project_scope(correction, tmp_path / "ea-587") == "same-project"


def test_sibling_worktree_writer_slug_is_same_project(monkeypatch, tmp_path):
    """authoritative slug と異なる writer slug は第二分岐で同一PJに畳む。"""
    monkeypatch.setattr("pj_slug.resolve_pj_slug", lambda root: "ea-597")
    monkeypatch.setattr(
        "rl_common.persistence.project_name_from_dir",
        lambda root: "evolve-anything",
    )
    correction = {"project_path": "evolve-anything", "message": "project detail"}
    assert correction["project_path"] != "ea-597"
    assert metrics._pillar2_project_scope(correction, tmp_path / "ea-597") == "same-project"


def test_unrelated_writer_slug_falls_through_to_classifier(monkeypatch, tmp_path):
    """どちらの slug とも一致しない project_path は畳まず既存の判定へ落とす。

    slug 照合の比較を真偽値へ潰す退行（``project_path == f(root)`` → ``f(root)``）は
    「一致する入力が same-project になる」ことだけを見るテストでは検出できず、
    他PJの correction が自PJの柱2へ計上される（corrections.jsonl は全PJ共有）。
    委譲先の返り値を sentinel にして、フォールスルー自体を固定する。
    """
    monkeypatch.setattr("pj_slug.resolve_pj_slug", lambda root: "ea-597")
    monkeypatch.setattr(
        "rl_common.persistence.project_name_from_dir",
        lambda root: "evolve-anything",
    )
    sentinel = "sentinel-scope"
    monkeypatch.setattr(metrics, "_classify_project_scope", lambda c, root: sentinel)
    correction = {"project_path": "other-pj", "message": "project detail"}
    assert metrics._pillar2_project_scope(correction, tmp_path / "ea-597") == sentinel


def test_same_reflection_is_deduplicated_by_normalized_key(tmp_path):
    second_base = _base(correction_id="d" * 32)
    second_events = [
        {**_events()[0], "correction_id": "e" * 32, "target_correction_id": "d" * 32,
         "correction_message_sha256": _hash_correction_message(second_base)},
        {**_events()[1], "correction_id": "f" * 32, "target_correction_id": "d" * 32,
         "confirms_attempt_id": "e" * 32},
    ]
    result = _count(tmp_path, [_base(), second_base], _events() + second_events)
    assert result["count"] == 1


def test_duplicate_event_ids_force_not_measured(tmp_path):
    second_base = _base(correction_id="d" * 32, extracted_learning="Second API")
    second_events = [
        {
            **_events()[0],
            "correction_id": "e" * 32,
            "target_correction_id": second_base["correction_id"],
            "reflect_target_path": "repo:.claude/rules/second.md",
            "correction_message_sha256": _hash_correction_message(second_base),
        },
        {
            **_events()[1],
            "target_correction_id": second_base["correction_id"],
            "confirms_attempt_id": "e" * 32,
        },
    ]

    result = _count(tmp_path, [_base(), second_base], _events() + second_events)

    assert result["count"] == 2
    assert result["health"]["duplicate_event_row_count"] == 2
    assert result["measured"] is False


def test_other_target_kind_is_excluded(tmp_path):
    events = _events()
    events[0]["reflect_target_kind"] = "other"
    result = _count(tmp_path, [_base()], events)
    assert result["count"] == 0
    assert result["other_kind_count"] == 1


def test_legacy_applied_forces_not_measured(tmp_path):
    result = _count(tmp_path, [_base()], [])
    assert result["count"] == 0
    assert result["legacy_unverified_count"] == 1
    assert result["measured"] is False


def test_pre_scheme_applied_baseline_is_complete_and_excluded_from_degradation(tmp_path, monkeypatch):
    bases = [
        _base(correction_id=correction_id, timestamp=f"2026-08-{index + 1:02d}T00:00:00Z")
        for index, correction_id in enumerate(sorted(BASELINE_IDS))
    ]
    _install_test_baseline(monkeypatch, bases)

    result = _count(tmp_path, bases, [])

    assert {correction_id for correction_id, _ in metrics.PRE_SCHEME_APPLIED_BASELINE} == BASELINE_IDS
    assert result["count"] == 0
    assert result["applied_list"] == []
    assert result["legacy_unverified_count"] == 0
    assert result["pre_scheme_excluded_count"] == 4
    assert result["measured"] is True
    assert result["health"]["degraded"] is False


def test_pre_scheme_baseline_mixed_with_legacy_applied_stays_unmeasured(tmp_path, monkeypatch):
    bases = [
        _base(correction_id=correction_id)
        for correction_id in sorted(BASELINE_IDS)
    ]
    _install_test_baseline(monkeypatch, bases)
    bases.append(_base(correction_id="d" * 32))

    result = _count(tmp_path, bases, [])

    assert result["count"] == 0
    assert result["applied_list"] == []
    assert result["legacy_unverified_count"] == 1
    assert result["pre_scheme_excluded_count"] == 4
    assert result["measured"] is False


def test_pre_scheme_baseline_reapplied_with_pillar2_events_is_counted(tmp_path, monkeypatch):
    correction_id = next(iter(BASELINE_IDS))
    base = _base(correction_id=correction_id)
    _install_test_baseline(monkeypatch, [base])
    events = [
        {
            **_events()[0],
            "target_correction_id": correction_id,
            "correction_message_sha256": _hash_correction_message(base),
        },
        {
            **_events()[1],
            "target_correction_id": correction_id,
        },
    ]

    result = _count(tmp_path, [base], events)

    assert correction_id in {item[0] for item in metrics.PRE_SCHEME_APPLIED_BASELINE}
    assert result["count"] == 1
    assert result["pre_scheme_excluded_count"] == 0
    assert result["reconciled_count"] == 0
    assert result["measured"] is True


def test_reapplied_baseline_is_counted_beside_unreapplied_baseline(tmp_path, monkeypatch):
    reapplied_id, unreapplied_id = sorted(BASELINE_IDS)[:2]
    reapplied_base = _base(correction_id=reapplied_id)
    unreapplied_base = _base(correction_id=unreapplied_id)
    _install_test_baseline(monkeypatch, [reapplied_base, unreapplied_base])
    events = [
        {
            **_events()[0],
            "target_correction_id": reapplied_id,
            "correction_message_sha256": _hash_correction_message(reapplied_base),
        },
        {
            **_events()[1],
            "target_correction_id": reapplied_id,
        },
    ]

    result = _count(
        tmp_path,
        [reapplied_base, unreapplied_base],
        events,
    )

    assert result["count"] == 1
    assert result["pre_scheme_excluded_count"] == 1
    assert result["measured"] is True


@pytest.mark.parametrize(
    ("overrides", "expected_invalidated_count"),
    [
        pytest.param(
            {
                "project_path": "other-project",
                "message": "Use /srv/other/project/data.sqlite only here",
            },
            0,
            id="other-project",
        ),
        pytest.param({"invalidated": True}, 1, id="invalidated"),
        pytest.param({"reflect_status": "pending"}, 0, id="non-applied"),
    ],
)
def test_baseline_classification_runs_after_scope_invalidation_and_status(
    tmp_path, monkeypatch, overrides, expected_invalidated_count
):
    correction_id = next(iter(BASELINE_IDS))
    base = _base(correction_id=correction_id, **overrides)
    _install_test_baseline(monkeypatch, [base])

    result = _count(tmp_path, [base], [])

    assert result["legacy_unverified_count"] == 0
    assert result["pre_scheme_excluded_count"] == 0
    assert result["invalidated_count"] == expected_invalidated_count
    assert result["measured"] is True


def test_pre_scheme_baseline_constants_pin_current_row_fingerprints():
    assert metrics.PRE_SCHEME_APPLIED_BASELINE == BASELINE_FINGERPRINTS


def test_changed_baseline_row_is_legacy_unverified(tmp_path, monkeypatch):
    base = _base(correction_id=next(iter(BASELINE_IDS)))
    _install_test_baseline(monkeypatch, [base])
    changed = {**base, "message": "modified after baseline approval"}

    result = _count(tmp_path, [changed], [])

    assert result["pre_scheme_excluded_count"] == 0
    assert result["legacy_unverified_count"] == 1
    assert result["measured"] is False


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-31T00:00:00Z",
        "2026-09-01T10:00:00Z",
        None,
        "2099-01-01T00:00:00Z",
    ],
    ids=["before-scheme", "after-scheme", "missing", "future"],
)
def test_non_baseline_legacy_applied_is_never_rescued_by_timestamp(tmp_path, timestamp):
    result = _count(
        tmp_path,
        [_base(correction_id="d" * 32, timestamp=timestamp)],
        [],
    )

    assert result["legacy_unverified_count"] == 1
    assert result["pre_scheme_excluded_count"] == 0
    assert result["measured"] is False


def test_post_scheme_positive_control_is_counted_and_not_baseline_excluded(tmp_path):
    base = _base(correction_id=POST_SCHEME_APPLIED_ID)
    events = [
        {
            **_events()[0],
            "target_correction_id": POST_SCHEME_APPLIED_ID,
            "correction_message_sha256": _hash_correction_message(base),
        },
        {
            **_events()[1],
            "target_correction_id": POST_SCHEME_APPLIED_ID,
        },
    ]

    result = _count(tmp_path, [base], events)

    assert POST_SCHEME_APPLIED_ID not in metrics.PRE_SCHEME_APPLIED_BASELINE
    assert result["count"] == 1
    assert result["pre_scheme_excluded_count"] == 0
    assert result["measured"] is True


def test_same_project_invalid_id_applied_forces_not_measured(tmp_path):
    result = _count(
        tmp_path,
        [_base(correction_id=None, project_path=str(tmp_path))],
        [],
    )

    assert result["count"] == 0
    assert result["measured"] is False
    assert result["health"]["invalid_base_id_applied_row_count"] == 1
    assert result["health"]["invalid_base_id_non_applied_row_count"] == 0
    assert result["health"]["invalid_base_id_applied_same_project_row_count"] == 1
    assert result["health"]["invalid_base_id_applied_global_looking_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_same_project_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_global_looking_row_count"] == 0


def test_global_looking_invalid_id_applied_forces_not_measured(tmp_path):
    result = _count(tmp_path, [_base(correction_id="")], [])

    assert result["measured"] is False
    assert result["health"]["invalid_base_id_applied_row_count"] == 1
    assert result["health"]["invalid_base_id_applied_same_project_row_count"] == 0
    assert result["health"]["invalid_base_id_applied_global_looking_row_count"] == 1


def test_other_project_invalid_id_applied_does_not_degrade(tmp_path):
    result = _count(
        tmp_path,
        [
            _base(
                correction_id=None,
                project_path="other-project",
                message="Use /srv/other/project/data.sqlite only here",
            )
        ],
        [],
    )

    assert result["measured"] is True
    assert result["health"]["invalid_base_id_applied_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_row_count"] == 0


def test_other_project_invalid_id_non_applied_is_out_of_scope(tmp_path):
    result = _count(
        tmp_path,
        [
            _base(
                correction_id=None,
                reflect_status="promoted",
                project_path="other-project",
                message="Use /srv/other/project/data.sqlite only here",
            )
        ],
        [],
    )

    assert result["health"]["invalid_base_id_non_applied_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_same_project_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_global_looking_row_count"] == 0


def test_invalidated_invalid_id_applied_does_not_degrade(tmp_path):
    result = _count(
        tmp_path,
        [_base(correction_id=None, invalidated=True)],
        [],
    )

    assert result["measured"] is True
    assert result["health"]["invalid_base_id_applied_row_count"] == 0


def test_invalid_id_non_applied_is_visible_but_does_not_degrade(tmp_path):
    result = _count(
        tmp_path,
        [_base(correction_id=None, reflect_status="pending")],
        [],
    )

    assert result["measured"] is True
    assert result["health"]["invalid_base_id_applied_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_row_count"] == 1
    assert result["health"]["invalid_base_id_non_applied_same_project_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_global_looking_row_count"] == 1


def test_same_project_invalid_id_non_applied_is_broken_down(tmp_path):
    result = _count(
        tmp_path,
        [
            _base(
                correction_id=None,
                reflect_status="promoted",
                project_path=str(tmp_path),
            )
        ],
        [],
    )

    assert result["health"]["invalid_base_id_non_applied_row_count"] == 1
    assert result["health"]["invalid_base_id_non_applied_same_project_row_count"] == 1
    assert result["health"]["invalid_base_id_non_applied_global_looking_row_count"] == 0


@pytest.mark.parametrize("correction_id", [0, [], "abc"])
def test_invalid_id_value_classes_force_not_measured(tmp_path, correction_id):
    result = _count(tmp_path, [_base(correction_id=correction_id)], [])

    assert result["measured"] is False
    assert result["health"]["invalid_base_id_applied_row_count"] == 1


def test_all_applied_rows_with_valid_ids_remain_measured(tmp_path):
    result = _count(tmp_path, [_base()], _events())

    assert result["count"] == 1
    assert result["measured"] is True
    assert result["health"]["invalid_base_id_applied_row_count"] == 0
    assert result["health"]["invalid_base_id_non_applied_row_count"] == 0


def test_other_project_valid_id_legacy_row_does_not_degrade(tmp_path):
    result = _count(
        tmp_path,
        [
            _base(
                project_path="other-project",
                message="Use /srv/other/project/data.sqlite only here",
            )
        ],
        [],
    )

    assert result["measured"] is True
    assert result["legacy_unverified_count"] == 0
