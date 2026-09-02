from datetime import datetime, timezone

import pytest

from reflect_fold import _hash_correction_message, fold_corrections


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
BASE_ID = "a" * 32
ATTEMPT_ID = "b" * 32
APPLIED_ID = "c" * 32
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
