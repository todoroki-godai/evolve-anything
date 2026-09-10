from datetime import datetime, timezone

import pytest

from reflect_fold import _hash_correction_message, fold_corrections


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
BASE_ID = "a" * 32
ATTEMPT_ID = "b" * 32
APPLIED_ID = "c" * 32
REVERT_ID = "f" * 32
MISSING = object()


def _base(**overrides):
    value = {
        "correction_id": BASE_ID,
        "extracted_learning": "Use the stable API",
        "reflect_status": "applied",
    }
    value.update(overrides)
    return value


def _attempt(**overrides):
    value = {
        "correction_id": ATTEMPT_ID,
        "schema_version": 1,
        "event_type": "correction_apply_attempted",
        "target_correction_id": BASE_ID,
        "reflect_target_kind": "project_rule",
        "reflect_target_path": "repo:.claude/rules/a.md",
        "reflect_draft_line": "Use the stable API",
        "correction_message_sha256": _hash_correction_message(_base()),
        "attempted_at": "2026-08-31T10:00:00+00:00",
    }
    value.update(overrides)
    return value


def _applied(**overrides):
    value = {
        "correction_id": APPLIED_ID,
        "schema_version": 1,
        "event_type": "correction_applied",
        "target_correction_id": BASE_ID,
        "confirms_attempt_id": ATTEMPT_ID,
        "reflect_applied_at": "2026-08-31T10:01:00+00:00",
    }
    value.update(overrides)
    return value


def _reverted(**overrides):
    value = {
        "correction_id": REVERT_ID,
        "schema_version": 1,
        "event_type": "correction_reverted",
        "reverts_applied_id": APPLIED_ID,
        "reverted_at": "2026-08-31T10:02:00+00:00",
        "revert_reason": "The reflected change was reverted",
    }
    value.update(overrides)
    return value


def test_revert_closes_latest_applied_and_prevents_reconciliation():
    folded, health = fold_corrections(
        [_base()], [_attempt(), _applied(), _reverted()], now=NOW
    )

    assert folded[0].has_pillar2_fields is False
    assert folded[0].reconciled is False
    assert folded[0].reverted is True
    assert health.stale_reverts == 0
    assert health.ambiguous_reverts == 0


def test_reverting_latest_applied_does_not_restore_older_applied():
    newer_attempt = _attempt(
        correction_id="d" * 32,
        attempted_at="2026-08-31T11:00:00+00:00",
        reflect_target_path="repo:.claude/rules/new.md",
    )
    newer_applied = _applied(
        correction_id="e" * 32,
        confirms_attempt_id="d" * 32,
        reflect_applied_at="2026-08-31T11:01:00+00:00",
    )
    revert_newer = _reverted(
        reverts_applied_id="e" * 32,
        reverted_at="2026-08-31T11:02:00+00:00",
    )

    folded, health = fold_corrections(
        [_base()],
        [_attempt(), _applied(), newer_attempt, newer_applied, revert_newer],
        now=NOW,
    )

    assert folded[0].has_pillar2_fields is False
    assert folded[0].reverted is True
    assert health.stale_reverts == 0


def test_applied_after_revert_becomes_active():
    later_attempt = _attempt(
        correction_id="d" * 32,
        attempted_at="2026-08-31T11:00:00+00:00",
        reflect_target_path="repo:.claude/rules/later.md",
    )
    later_applied = _applied(
        correction_id="e" * 32,
        confirms_attempt_id="d" * 32,
        reflect_applied_at="2026-08-31T11:01:00+00:00",
    )

    folded, health = fold_corrections(
        [_base()],
        [_attempt(), _applied(), _reverted(), later_attempt, later_applied],
        now=NOW,
    )

    assert folded[0].has_pillar2_fields is True
    assert folded[0].reverted is False
    assert folded[0].reflect_applied_id == "e" * 32
    assert folded[0].reflect_target_path == "repo:.claude/rules/later.md"
    assert health.stale_reverts == 0
    assert health.ambiguous_reverts == 0


def test_revert_is_compare_and_set_against_current_applied():
    newer_attempt = _attempt(
        correction_id="d" * 32,
        attempted_at="2026-08-31T11:00:00+00:00",
        reflect_target_path="repo:.claude/rules/new.md",
    )
    newer_applied = _applied(
        correction_id="e" * 32,
        confirms_attempt_id="d" * 32,
        reflect_applied_at="2026-08-31T11:01:00+00:00",
    )

    folded, health = fold_corrections(
        [_base()],
        [
            _attempt(),
            _applied(),
            newer_attempt,
            newer_applied,
            _reverted(reverted_at="2026-08-31T11:02:00+00:00"),
        ],
        now=NOW,
    )

    assert folded[0].has_pillar2_fields is True
    assert folded[0].reflect_applied_id == "e" * 32
    assert health.stale_reverts == 1


def test_duplicate_revert_is_stale_after_first_closes_state():
    second_revert = _reverted(
        correction_id="1" * 32,
        reverted_at="2026-08-31T10:03:00+00:00",
    )

    folded, health = fold_corrections(
        [_base()], [_attempt(), _applied(), _reverted(), second_revert], now=NOW
    )

    assert folded[0].has_pillar2_fields is False
    assert folded[0].reverted is True
    assert health.stale_reverts == 1


def test_revert_with_missing_applied_reference_is_stale():
    folded, health = fold_corrections(
        [_base()], [_reverted(reverts_applied_id="1" * 32)], now=NOW
    )

    assert folded[0].has_pillar2_fields is False
    assert health.stale_reverts == 1


@pytest.mark.parametrize(
    "reverted_at",
    [
        pytest.param("2026-08-31T10:00:00+00:00", id="before-applied"),
        pytest.param("2026-08-31T10:01:00+00:00", id="same-time"),
    ],
)
def test_ambiguous_revert_excludes_target(reverted_at):
    folded, health = fold_corrections(
        [_base()],
        [_attempt(), _applied(), _reverted(reverted_at=reverted_at)],
        now=NOW,
    )

    assert folded[0].has_pillar2_fields is False
    assert folded[0].ambiguous_revert is True
    assert health.ambiguous_reverts == 1
    assert health.stale_reverts == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("correction_id", "bad"),
        ("reverts_applied_id", "bad"),
        ("reverted_at", "2026-08-31T10:02:00"),
        ("revert_reason", ""),
        ("revert_reason", "line one\nline two"),
        ("schema_version", 2),
    ],
)
def test_invalid_revert_uses_existing_invalid_event_health(field, value):
    event = _reverted()
    event[field] = value

    folded, health = fold_corrections(
        [_base()], [_attempt(), _applied(), event], now=NOW
    )

    assert folded[0].has_pillar2_fields is True
    assert health.invalid_events == 1
    assert health.stale_reverts == 0


def test_revert_fold_is_invariant_to_input_order():
    chronological = [_attempt(), _applied(), _reverted()]
    for events in (chronological, list(reversed(chronological))):
        folded, health = fold_corrections([_base()], events, now=NOW)
        assert folded[0].has_pillar2_fields is False
        assert health.stale_reverts == 0
        assert health.ambiguous_reverts == 0


def test_fold_selects_latest_by_timestamp_not_order():
    newer_attempt = _attempt(
        correction_id="d" * 32,
        reflect_target_path="repo:.claude/rules/new.md",
        attempted_at="2026-08-31T12:00:00+00:00",
    )
    newer_applied = _applied(
        correction_id="e" * 32,
        confirms_attempt_id="d" * 32,
        reflect_applied_at="2026-08-31T12:01:00+00:00",
    )
    for events in ([newer_applied, newer_attempt, _applied(), _attempt()], [_attempt(), _applied(), newer_attempt, newer_applied]):
        folded, health = fold_corrections([_base()], events, now=NOW)
        assert folded[0].reflect_applied_at == "2026-08-31T12:01:00+00:00"
        assert folded[0].reflect_target_path == "repo:.claude/rules/new.md"
        assert health.invalid_events == 0


def test_legacy_folded_correction_has_pillar2_fields_false():
    folded, _ = fold_corrections([_base()], [], now=NOW)
    assert folded[0].has_pillar2_fields is False


def test_reconciles_from_attempted_event_when_confirmation_missing():
    folded, _ = fold_corrections([_base()], [_attempt()], now=NOW)
    assert folded[0].has_pillar2_fields is True
    assert folded[0].reconciled is True


def test_confirmation_positive_control_is_not_reconciled():
    folded, _ = fold_corrections([_base()], [_attempt(), _applied()], now=NOW)
    assert folded[0].has_pillar2_fields is True
    assert folded[0].reconciled is False


def test_duplicate_base_rows_excluded_and_flagged():
    folded, health = fold_corrections(
        [_base(invalidated=True), _base(invalidated=False)], [_attempt(), _applied()], now=NOW
    )
    assert folded == []
    assert health.duplicate_base_row_count == 2


def test_duplicate_event_ids_are_excluded_and_flagged():
    second_base = _base(correction_id="d" * 32, extracted_learning="Second API")
    second_attempt = _attempt(
        correction_id="e" * 32,
        target_correction_id=second_base["correction_id"],
        correction_message_sha256=_hash_correction_message(second_base),
    )
    duplicate_applied = _applied(
        target_correction_id=second_base["correction_id"],
        confirms_attempt_id=second_attempt["correction_id"],
    )

    folded, health = fold_corrections(
        [_base(), second_base],
        [_attempt(), _applied(), second_attempt, duplicate_applied],
        now=NOW,
    )

    assert [item.has_pillar2_fields for item in folded] == [True, True]
    assert [item.reconciled for item in folded] == [True, True]
    assert health.duplicate_event_row_count == 2


def test_skipped_event_own_id_collision_is_excluded_and_flagged():
    skipped = {
        "correction_id": ATTEMPT_ID,
        "schema_version": 1,
        "event_type": "correction_skipped",
        "target_correction_id": BASE_ID,
        "skipped_at": "2026-08-31T10:02:00+00:00",
    }

    folded, health = fold_corrections(
        [_base()], [_attempt(), _applied(), skipped], now=NOW
    )

    assert folded[0].has_pillar2_fields is False
    assert health.duplicate_event_row_count == 2


def test_skipped_event_with_timestamp_does_not_affect_fold_or_validation():
    skipped = {
        "correction_id": "d" * 32,
        "schema_version": 1,
        "event_type": "correction_skipped",
        "target_correction_id": BASE_ID,
        "skipped_at": "2026-08-31T10:02:00+00:00",
    }

    folded, health = fold_corrections([_base()], [skipped], now=NOW)

    assert folded[0].has_pillar2_fields is False
    assert folded[0].reflect_applied_at is None
    assert health.invalid_events == 0
    assert health.duplicate_event_row_count == 0


@pytest.mark.parametrize(
    "correction_id",
    [
        pytest.param(MISSING, id="absent"),
        pytest.param(None, id="none"),
        pytest.param("", id="empty"),
        pytest.param(0, id="integer"),
        pytest.param([], id="list"),
        pytest.param("abc", id="invalid-format"),
    ],
)
def test_invalid_base_ids_are_excluded_and_classified(correction_id):
    base = _base()
    if correction_id is MISSING:
        base.pop("correction_id")
    else:
        base["correction_id"] = correction_id

    folded, health = fold_corrections([base], [], now=NOW)

    assert folded == []
    assert health.invalid_base_id_records == [base]


def test_invalid_base_id_non_applied_is_classified_separately():
    base = _base(correction_id=None, reflect_status="pending")

    folded, health = fold_corrections([base], [], now=NOW)

    assert folded == []
    assert health.invalid_base_id_records == [base]


def test_valid_base_id_is_positive_control_for_invalid_id_health():
    folded, health = fold_corrections([_base()], [], now=NOW)

    assert len(folded) == 1
    assert health.invalid_base_id_records == []


def test_invalid_attempt_event_rejected():
    folded, health = fold_corrections(
        [_base()], [_attempt(correction_message_sha256="not-a-hash")], now=NOW
    )
    assert folded[0].has_pillar2_fields is False
    assert health.invalid_events == 1


def test_orphan_applied_confirmation_is_degraded():
    folded, health = fold_corrections(
        [_base()], [_applied(confirms_attempt_id="f" * 32)], now=NOW
    )
    assert folded[0].has_pillar2_fields is False
    assert health.orphan_confirmations == 1


def test_attempt_hash_mismatch_rejected():
    folded, health = fold_corrections(
        [_base()], [_attempt(correction_message_sha256="0" * 64), _applied()], now=NOW
    )
    assert folded[0].has_pillar2_fields is False
    assert health.hash_mismatch_count == 1
    assert health.orphan_confirmations == 1


def test_naive_timestamp_is_invalid():
    folded, health = fold_corrections(
        [_base()], [_attempt(attempted_at="2026-08-31T10:00:00")], now=NOW
    )
    assert folded[0].has_pillar2_fields is False
    assert health.invalid_events == 1


def test_duplicate_confirmations_are_both_rejected():
    duplicate = _applied(correction_id="d" * 32)
    folded, health = fold_corrections([_base()], [_attempt(), _applied(), duplicate], now=NOW)
    assert folded[0].has_pillar2_fields is True
    assert folded[0].reconciled is True
    assert health.duplicate_confirmations == 2


def test_confirmation_target_mismatch_is_degraded():
    folded, health = fold_corrections(
        [_base()], [_attempt(), _applied(target_correction_id="f" * 32)], now=NOW
    )
    assert folded[0].has_pillar2_fields is True
    assert folded[0].reconciled is True
    assert health.orphan_confirmations == 1


def test_unknown_schema_is_degraded():
    folded, health = fold_corrections([_base()], [_attempt(schema_version=2)], now=NOW)
    assert folded[0].has_pillar2_fields is False
    assert health.unknown_schema_events == 1


def test_old_orphan_attempt_is_expected_after_decay():
    old_attempt = _attempt(
        target_correction_id="f" * 32,
        attempted_at="2026-05-01T00:00:00+00:00",
    )
    old_applied = _applied(
        target_correction_id="f" * 32,
        reflect_applied_at="2026-05-01T00:01:00+00:00",
    )
    _, health = fold_corrections([], [old_attempt, old_applied], now=NOW, decay_grace_days=90)
    assert health.orphan_events_expected == 1
    assert health.orphan_events_unexpected == 0


def test_unknown_event_type_does_not_affect_fold():
    folded, health = fold_corrections(
        [_base()], [{**_attempt(), "event_type": "future_event"}], now=NOW
    )
    assert folded[0].has_pillar2_fields is False
    assert health.invalid_events == 0
    assert health.unknown_schema_events == 0


def test_message_hash_normalizes_unicode_nfc():
    decomposed = "Cafe\u0301"
    composed_hash = _hash_correction_message({"extracted_learning": "Café"})
    folded, health = fold_corrections(
        [_base(extracted_learning=decomposed)],
        [_attempt(correction_message_sha256=composed_hash), _applied()],
        now=NOW,
    )
    assert folded[0].has_pillar2_fields is True
    assert health.hash_mismatch_count == 0
