"""daily review 限定の rephrase similarity 重複除外（#543）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import daily_review as dr  # noqa: E402
from weak_signals.detectors import detect_rephrase  # noqa: E402
from weak_signals.rephrase_dedup import (  # noqa: E402
    _EXCLUDED_FIELDS,
    _REQUIRED_FIELDS,
    expand_seen_keys_for_rephrase_dupes,
)
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


def _rec(
    key: str,
    *,
    similarity: float = 0.9,
    session_id: str = "session-1",
    channel: str = "rephrase",
    **provenance,
) -> dict:
    prov = {
        "detector": "rephrase",
        "similarity": similarity,
        "prev_line_no": 10,
        "line_no": 11,
        "prev_text": "前の発話です",
        "text": "修正後の発話です",
        "source_path": "/sessions/one.jsonl",
    }
    prov.update(provenance)
    return {
        "channel": channel,
        "provenance": prov,
        "session_id": session_id,
        "signal_key": key,
    }


def _signal(*, similarity: float, line_no: int = 11, prev_line_no: int = 10) -> WeakSignal:
    return WeakSignal(
        channel="rephrase",
        provenance={
            "detector": "rephrase",
            "similarity": similarity,
            "prev_line_no": prev_line_no,
            "line_no": line_no,
            "prev_text": "開発サーバーを起動して確認してください",
            "text": "開発サーバーを起動して目視確認してください",
            "source_path": "/sessions/one.jsonl",
        },
        detected_at="2026-08-25T00:00:00+00:00",
        session_id="session-1",
        pj_slug="evolve-anything",
    )


def _write_seen(path: Path, keys: set[str]) -> None:
    path.write_text(
        "".join(json.dumps({"key": key}) + "\n" for key in sorted(keys)),
        encoding="utf-8",
    )


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def test_similarity_only_pairs_expand_to_exact_unread_delta() -> None:
    records = []
    seen = set()
    expected = set()
    for index in range(6):
        base = _rec(
            f"seen-{index}",
            similarity=0.9000,
            session_id=f"session-{index}",
            line_no=index + 20,
            prev_line_no=index + 19,
        )
        duplicate = _rec(
            f"unread-{index}",
            similarity=0.9001,
            session_id=f"session-{index}",
            line_no=index + 20,
            prev_line_no=index + 19,
        )
        records.extend((base, duplicate))
        seen.add(base["signal_key"])
        expected.add(duplicate["signal_key"])

    expanded = expand_seen_keys_for_rephrase_dupes(records, seen)

    assert expanded - seen == expected
    assert seen <= expanded


@pytest.mark.parametrize("field", _REQUIRED_FIELDS)
def test_missing_required_provenance_field_fails_safe(field: str) -> None:
    missing_a = _rec("seen", **{field: ""})
    missing_b = _rec("unread", similarity=0.8, **{field: ""})

    assert expand_seen_keys_for_rephrase_dupes([missing_a, missing_b], {"seen"}) == {"seen"}


def test_missing_session_and_missing_original_record_fail_safe() -> None:
    assert expand_seen_keys_for_rephrase_dupes(
        [_rec("seen", session_id=""), _rec("unread", similarity=0.8, session_id="")],
        {"seen"},
    ) == {"seen"}
    assert expand_seen_keys_for_rephrase_dupes([_rec("unread")], {"missing-original"}) == {
        "missing-original"
    }


def test_nfc_and_edge_whitespace_are_normalized_for_text_fields() -> None:
    seen = _rec("seen", text="  ばけて\n", prev_text="\t前です ")
    unread = _rec(
        "unread",
        similarity=0.8,
        text="は\u3099けて",
        prev_text="前です",
    )

    assert expand_seen_keys_for_rephrase_dupes([seen, unread], {"seen"}) == {
        "seen",
        "unread",
    }


@pytest.mark.parametrize(("first_similarity", "second_similarity"), [(0.0, 1.0), (-1.0, 2.0)])
def test_similarity_value_boundaries_do_not_affect_identity(
    first_similarity: float, second_similarity: float
) -> None:
    records = [
        _rec("seen", similarity=first_similarity),
        _rec("unread", similarity=second_similarity),
    ]

    assert expand_seen_keys_for_rephrase_dupes(records, {"seen"}) == {"seen", "unread"}


def test_json_escapes_and_input_order_do_not_change_expansion() -> None:
    records = [
        _rec("seen", text='引用 "A" と \\path\n次行'),
        _rec("unread", similarity=0.8, text='引用 "A" と \\path\n次行'),
    ]

    forward = expand_seen_keys_for_rephrase_dupes(records, {"seen"})
    reverse = expand_seen_keys_for_rephrase_dupes(list(reversed(records)), {"seen"})

    assert forward == reverse == {"seen", "unread"}


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("session_id", "session-2"),
        ("source_path", "/sessions/two.jsonl"),
        ("line_no", 12),
        ("prev_line_no", 9),
        ("detector", "future-rephrase"),
    ],
)
def test_identity_keeps_physical_and_session_fields_distinct(change: str, value: object) -> None:
    seen = _rec("seen")
    kwargs = {change: value}
    unread = _rec("unread", similarity=0.8, **kwargs)

    assert expand_seen_keys_for_rephrase_dupes([seen, unread], {"seen"}) == {"seen"}


def test_denylist_keeps_future_fields_and_only_excludes_similarity() -> None:
    same_future = [_rec("seen", future_field="v1"), _rec("dup", similarity=0.8, future_field="v1")]
    changed_future = _rec("different", similarity=0.7, future_field="v2")

    expanded = expand_seen_keys_for_rephrase_dupes(same_future + [changed_future], {"seen"})

    assert _EXCLUDED_FIELDS == frozenset({"similarity"})
    assert expanded == {"seen", "dup"}


def test_non_rephrase_channels_are_not_merged() -> None:
    for channel in ("llm_judge", "permission_deny", "verbosity"):
        records = [_rec("seen", channel=channel), _rec("unread", channel=channel, similarity=0.8)]
        assert expand_seen_keys_for_rephrase_dupes(records, {"seen"}) == {"seen"}


def test_detect_rephrase_provenance_contract_is_explicit() -> None:
    signals = detect_rephrase(
        [
            {
                "session_id": "s1",
                "line_no": 1,
                "text": "開発サーバー動かして。目視してみる。",
                "source_path": "/x.jsonl",
            },
            {
                "session_id": "s1",
                "line_no": 2,
                "text": "開発サーバー動かして。目視してみる",
                "source_path": "/x.jsonl",
            },
        ],
        "evolve-anything",
    )

    assert len(signals) == 1
    assert frozenset(signals[0].provenance) == _EXCLUDED_FIELDS | frozenset(_REQUIRED_FIELDS)


def test_184_seen_keys_with_corresponding_records_are_accepted() -> None:
    records = [
        _rec(
            f"key-{index}",
            session_id=f"session-{index}",
            line_no=index + 2,
            prev_line_no=index + 1,
        )
        for index in range(184)
    ]
    seen = {record["signal_key"] for record in records}

    assert expand_seen_keys_for_rephrase_dupes(records, seen) == seen


def test_build_review_uses_expanded_keys_and_surfaces_exact_count(tmp_path: Path) -> None:
    weak_signals = tmp_path / "weak_signals.jsonl"
    seen_path = tmp_path / "correction_review_seen.jsonl"
    first = _signal(similarity=0.9000)
    duplicate = _signal(similarity=0.9001)
    distinct = _signal(similarity=0.9002, line_no=13, prev_line_no=12)
    append_signals([first, duplicate, distinct], path=weak_signals)
    _write_seen(seen_path, {first.signal_key})

    review = dr.build_review(
        "evolve-anything",
        weak_signals_path=weak_signals,
        seen_path=seen_path,
        marker_base=tmp_path,
    )

    surfaced = {key for group in review["groups"] for key in group["signal_keys"]}
    assert duplicate.signal_key not in surfaced
    assert distinct.signal_key in surfaced
    assert review["rephrase_similarity_dedup_count"] == 1


def test_excluded_bootstrap_key_is_not_counted_as_rephrase_dedup(tmp_path: Path) -> None:
    weak_signals = tmp_path / "weak_signals.jsonl"
    seen_path = tmp_path / "correction_review_seen.jsonl"
    first = _signal(similarity=0.9000)
    duplicate = _signal(similarity=0.9001)
    append_signals([first, duplicate], path=weak_signals)
    _write_seen(seen_path, {first.signal_key})

    review = dr.build_review(
        "evolve-anything",
        weak_signals_path=weak_signals,
        seen_path=seen_path,
        exclude_signal_keys={duplicate.signal_key},
        marker_base=tmp_path,
    )

    assert review["rephrase_similarity_dedup_count"] == 0


def test_two_unread_similarity_duplicates_remain_one_review_group(tmp_path: Path) -> None:
    weak_signals = tmp_path / "weak_signals.jsonl"
    first = _signal(similarity=0.9000)
    duplicate = _signal(similarity=0.9001)
    append_signals([first, duplicate], path=weak_signals)

    review = dr.build_review(
        "evolve-anything",
        weak_signals_path=weak_signals,
        seen_path=tmp_path / "seen.jsonl",
        marker_base=tmp_path,
    )

    assert len(review["groups"]) == 1
    assert set(review["groups"][0]["signal_keys"]) == {first.signal_key, duplicate.signal_key}
    assert review["rephrase_similarity_dedup_count"] == 0


def test_build_review_dry_run_has_no_store_or_filesystem_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rl_common

    weak_signals = tmp_path / "weak_signals.jsonl"
    seen_path = tmp_path / "correction_review_seen.jsonl"
    first = _signal(similarity=0.9000)
    duplicate = _signal(similarity=0.9001)
    append_signals([first, duplicate], path=weak_signals)
    _write_seen(seen_path, {first.signal_key})
    before = _snapshot(tmp_path)

    def reject_write(*_args, **_kwargs):
        raise AssertionError("dry-run attempted a store write")

    monkeypatch.setattr(rl_common, "store_write", reject_write)
    monkeypatch.setattr(rl_common, "store_write_raw", reject_write)
    dr.build_review(
        "evolve-anything",
        weak_signals_path=weak_signals,
        seen_path=seen_path,
        dry_run=True,
        marker_base=tmp_path,
    )

    assert _snapshot(tmp_path) == before


def test_rephrase_dedup_module_contains_no_direct_write_primitives() -> None:
    source = (_lib_dir / "weak_signals" / "rephrase_dedup.py").read_text(encoding="utf-8")
    forbidden = ("store_write_raw", "write_text(", "json.dump(", "open(")
    assert not any(token in source for token in forbidden)
