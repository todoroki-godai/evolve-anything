import sys
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from rl_common.correction_id import (
    find_duplicate_ids,
    has_duplicate_id,
    new_correction_id,
    resolve_correction_id,
    validate_correction_id,
)


def test_new_ids_have_required_type_format_and_uniqueness():
    values = [new_correction_id() for _ in range(100)]
    assert all(isinstance(value, str) and validate_correction_id(value) for value in values)
    assert len(set(values)) == len(values)


@pytest.mark.parametrize(
    "value",
    [None, "", 1, [], {}, True, "a" * 31, "a" * 33, "A" + "a" * 31, "g" * 32],
)
def test_validator_rejects_all_boundary_values(value):
    assert validate_correction_id(value) is False


def test_duplicate_predicates_share_valid_id_contract():
    duplicate = "d" * 32
    records = [
        {"correction_id": duplicate},
        {"correction_id": duplicate},
        {"correction_id": "INVALID"},
        ["not", "a", "record"],
    ]
    assert has_duplicate_id(records, duplicate)
    assert find_duplicate_ids(records) == {duplicate: 2}


def test_resolver_is_unambiguous_and_returns_original_record_object():
    correction_id = "c" * 32
    record = {"correction_id": correction_id, "message": "original"}
    records = [record, ["malformed"]]
    result = resolve_correction_id(records, correction_id)
    assert result.status == "found"
    assert result.record is record
    assert result.match_count == 1


def test_resolver_reports_invalid_not_found_and_ambiguous():
    correction_id = "b" * 32
    assert resolve_correction_id([], None).status == "invalid_id"
    assert resolve_correction_id([], correction_id).status == "not_found"
    result = resolve_correction_id(
        [{"correction_id": correction_id}, {"correction_id": correction_id}], correction_id
    )
    assert result.status == "ambiguous"
    assert result.match_count == 2


def test_validator_monkeypatch_changes_append_duplicate_and_resolver_together(monkeypatch, tmp_path):
    """validator/duplicate predicate が保存とresolverへ実際に配線された単一ソース。"""
    import rl_common.correction_id as module

    monkeypatch.setattr(module, "validate_correction_id", lambda value: True)
    path = tmp_path / "corrections.jsonl"
    assert module.append_correction_record(path, {"correction_id": "x"}).status == "appended"
    assert module.resolve_correction_id([{"correction_id": "x"}], "x").status == "found"

    monkeypatch.setattr(module, "has_duplicate_id", lambda records, correction_id: True)
    assert module.append_correction_record(path, {"correction_id": "y"}).status == "duplicate_id"
