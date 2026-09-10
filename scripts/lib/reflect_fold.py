"""corrections と柱2反映イベントを検証しながら結合する。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from reflect_apply_match import classify_reflect_target_kind
from rl_common.correction_id import find_duplicate_ids, validate_correction_id


KNOWN_TARGET_KINDS = frozenset({
    "global_rule",
    "project_rule",
    "global_claude_md",
    "project_claude_md",
    "skill",
    "global_refs",
    "project_memory",
    "other",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TZ_AWARE_RE = re.compile(r".+[+-]\d{2}:\d{2}$")


def _read_reflect_target_kind(event: dict) -> Optional[str]:
    """過去に other で記録された新しい柱2対象だけ読み出し時に再分類する。"""
    recorded_kind = event.get("reflect_target_kind")
    if recorded_kind != "other":
        return recorded_kind
    target_path = event.get("reflect_target_path")
    global_root = Path.home() / ".claude"
    normalized_prefix = f"{global_root}:"
    if target_path.startswith(normalized_prefix):
        target_path = str(global_root / target_path.removeprefix(normalized_prefix))
    classified_kind = classify_reflect_target_kind(target_path)
    if classified_kind in {"global_refs", "project_memory"}:
        return classified_kind
    return recorded_kind


def _parse_iso8601_utc(raw) -> Optional[datetime]:
    """timezone 付き ISO8601 timestamp を aware UTC datetime に変換する。"""
    if not isinstance(raw, str) or not raw:
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if not _TZ_AWARE_RE.match(value):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def _hash_correction_message(base: dict) -> Optional[str]:
    """基底 correction の本文を正規化し SHA256 を返す。"""
    text = base.get("extracted_learning") or base.get("message")
    if not isinstance(text, str) or not text:
        return None
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class FoldedCorrection:
    base: dict
    reflect_applied_id: Optional[str] = None
    reflect_applied_at: Optional[str] = None
    reflect_target_kind: Optional[str] = None
    reflect_target_path: Optional[str] = None
    reflect_draft_line: Optional[str] = None
    correction_message_sha256: Optional[str] = None
    has_pillar2_fields: bool = False
    reconciled: bool = False


@dataclass
class FoldHealth:
    orphan_events_unexpected: int = 0
    orphan_events_expected: int = 0
    unknown_schema_events: int = 0
    invalid_events: int = 0
    duplicate_base_row_count: int = 0
    duplicate_event_row_count: int = 0
    orphan_confirmations: int = 0
    duplicate_confirmations: int = 0
    hash_mismatch_count: int = 0
    stale_reverts: int = 0
    ambiguous_reverts: int = 0
    invalid_base_id_records: list[dict] = field(default_factory=list, repr=False)


def _attempt_is_valid(event: dict) -> bool:
    if not validate_correction_id(event.get("correction_id")):
        return False
    if not validate_correction_id(event.get("target_correction_id")):
        return False
    if event.get("reflect_target_kind") not in KNOWN_TARGET_KINDS:
        return False
    path = event.get("reflect_target_path")
    if not isinstance(path, str) or not path:
        return False
    draft = event.get("reflect_draft_line")
    if not isinstance(draft, str) or not draft:
        return False
    sha256 = event.get("correction_message_sha256")
    return isinstance(sha256, str) and bool(_SHA256_RE.fullmatch(sha256))


def _applied_is_valid(event: dict) -> bool:
    return (
        validate_correction_id(event.get("correction_id"))
        and validate_correction_id(event.get("target_correction_id"))
        and validate_correction_id(event.get("confirms_attempt_id"))
    )


def _reverted_is_valid(event: dict) -> bool:
    reason = event.get("revert_reason")
    return (
        event.get("schema_version") == 1
        and validate_correction_id(event.get("correction_id"))
        and validate_correction_id(event.get("reverts_applied_id"))
        and _parse_iso8601_utc(event.get("reverted_at")) is not None
        and isinstance(reason, str)
        and bool(reason.strip())
        and "\n" not in reason
        and "\r" not in reason
    )


def fold_corrections(
    base_records: list,
    event_records: list,
    *,
    now: Optional[datetime] = None,
    decay_grace_days: int = 90,
) -> tuple[list[FoldedCorrection], FoldHealth]:
    """基底 correction と append-only の反映イベントを fail-closed で結合する。"""
    now = now or datetime.now(timezone.utc)
    health = FoldHealth()

    duplicate_counts = find_duplicate_ids(
        [record for record in base_records if isinstance(record, dict)]
    )
    duplicate_ids = set(duplicate_counts)
    health.duplicate_base_row_count = sum(duplicate_counts.values())

    bases_by_id: dict[str, dict] = {}
    order: list[str] = []
    for record in base_records:
        if not isinstance(record, dict):
            continue
        correction_id = record.get("correction_id")
        if not validate_correction_id(correction_id):
            health.invalid_base_id_records.append(record)
            continue
        if correction_id in duplicate_ids:
            continue
        if correction_id not in bases_by_id:
            order.append(correction_id)
        bases_by_id[correction_id] = record

    folded_by_id = {
        correction_id: FoldedCorrection(base=bases_by_id[correction_id])
        for correction_id in order
    }

    duplicate_event_counts = find_duplicate_ids(
        [record for record in event_records if isinstance(record, dict)]
    )
    duplicate_event_ids = set(duplicate_event_counts)
    health.duplicate_event_row_count = sum(duplicate_event_counts.values())

    attempts_by_own_id: dict[str, dict] = {}
    attempted_by_target: dict[str, list[dict]] = {}
    applied_events: list[dict] = []
    reverted_events: list[dict] = []
    for event in event_records:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if event_type not in (
            "correction_apply_attempted",
            "correction_applied",
            "correction_reverted",
        ):
            continue
        if event.get("correction_id") in duplicate_event_ids:
            continue
        if event_type == "correction_reverted":
            if not _reverted_is_valid(event):
                health.invalid_events += 1
                continue
            reverted_events.append(event)
            continue
        if event.get("schema_version") != 1:
            health.unknown_schema_events += 1
            continue
        if event_type == "correction_apply_attempted":
            if not _attempt_is_valid(event):
                health.invalid_events += 1
                continue
            if _parse_iso8601_utc(event.get("attempted_at")) is None:
                health.invalid_events += 1
                continue
            target_id = event.get("target_correction_id")
            base = bases_by_id.get(target_id)
            if base is not None:
                expected_hash = _hash_correction_message(base)
                if (
                    expected_hash is not None
                    and event.get("correction_message_sha256") != expected_hash
                ):
                    health.hash_mismatch_count += 1
                    continue
            attempts_by_own_id[event["correction_id"]] = event
            attempted_by_target.setdefault(target_id, []).append(event)
        else:
            if not _applied_is_valid(event):
                health.invalid_events += 1
                continue
            if _parse_iso8601_utc(event.get("reflect_applied_at")) is None:
                health.invalid_events += 1
                continue
            applied_events.append(event)

    confirmation_count: dict[str, int] = {}
    for event in applied_events:
        attempt_id = event["confirms_attempt_id"]
        confirmation_count[attempt_id] = confirmation_count.get(attempt_id, 0) + 1

    applied_by_target: dict[str, list[tuple[dict, dict]]] = {}
    applied_by_own_id: dict[str, tuple[dict, dict]] = {}
    for event in applied_events:
        attempt_id = event["confirms_attempt_id"]
        if confirmation_count[attempt_id] > 1:
            health.duplicate_confirmations += 1
            continue
        attempt = attempts_by_own_id.get(attempt_id)
        if attempt is None:
            health.orphan_confirmations += 1
            continue
        if attempt.get("target_correction_id") != event.get("target_correction_id"):
            health.orphan_confirmations += 1
            continue
        pair = (event, attempt)
        applied_by_target.setdefault(event["target_correction_id"], []).append(pair)
        applied_by_own_id[event["correction_id"]] = pair

    def latest_pair(pairs: list[tuple[dict, dict]]) -> Optional[tuple[dict, dict]]:
        if not pairs:
            return None
        return max(
            pairs,
            key=lambda pair: (
                _parse_iso8601_utc(pair[0].get("reflect_applied_at")),
                pair[0].get("correction_id", ""),
            ),
        )

    def latest_attempt(events: list[dict]) -> Optional[dict]:
        if not events:
            return None
        return max(
            events,
            key=lambda event: (
                _parse_iso8601_utc(event.get("attempted_at")),
                event.get("correction_id", ""),
            ),
        )

    reverts_by_target: dict[str, list[dict]] = {}
    for event in reverted_events:
        pair = applied_by_own_id.get(event["reverts_applied_id"])
        if pair is None:
            health.stale_reverts += 1
            continue
        target_id = pair[0]["target_correction_id"]
        reverts_by_target.setdefault(target_id, []).append(event)

    active_by_target: dict[str, tuple[dict, dict]] = {}
    reverted_targets: set[str] = set()
    ambiguous_targets: set[str] = set()
    for target_id in applied_by_target.keys() | reverts_by_target.keys():
        pairs = applied_by_target.get(target_id, [])
        applied_timestamps = {
            _parse_iso8601_utc(pair[0].get("reflect_applied_at")) for pair in pairs
        }
        usable_reverts = []
        for event in reverts_by_target.get(target_id, []):
            reverted_at = _parse_iso8601_utc(event.get("reverted_at"))
            referenced = applied_by_own_id[event["reverts_applied_id"]][0]
            referenced_at = _parse_iso8601_utc(referenced.get("reflect_applied_at"))
            if reverted_at < referenced_at or reverted_at in applied_timestamps:
                health.ambiguous_reverts += 1
                ambiguous_targets.add(target_id)
                continue
            usable_reverts.append(event)

        transitions = [
            (
                _parse_iso8601_utc(pair[0].get("reflect_applied_at")),
                pair[0].get("correction_id", ""),
                "applied",
                pair,
            )
            for pair in pairs
        ]
        transitions.extend(
            (
                _parse_iso8601_utc(event.get("reverted_at")),
                event.get("correction_id", ""),
                "reverted",
                event,
            )
            for event in usable_reverts
        )
        current: Optional[tuple[dict, dict]] = None
        for _, _, transition_type, payload in sorted(transitions):
            if transition_type == "applied":
                current = payload
                continue
            if (
                current is None
                or current[0].get("correction_id")
                != payload.get("reverts_applied_id")
            ):
                health.stale_reverts += 1
                continue
            current = None
            reverted_targets.add(target_id)
        if current is not None:
            active_by_target[target_id] = current

    for target_id, pair in active_by_target.items():
        if target_id in ambiguous_targets:
            continue
        if target_id not in folded_by_id:
            timestamp = _parse_iso8601_utc(pair[0].get("reflect_applied_at"))
            if timestamp is not None and (now - timestamp).days > decay_grace_days:
                health.orphan_events_expected += 1
            else:
                health.orphan_events_unexpected += 1
            continue
        applied_event, attempt_event = pair
        folded = folded_by_id[target_id]
        folded.reflect_applied_id = applied_event.get("correction_id")
        folded.reflect_applied_at = applied_event.get("reflect_applied_at")
        folded.reflect_target_kind = _read_reflect_target_kind(attempt_event)
        folded.reflect_target_path = attempt_event.get("reflect_target_path")
        folded.reflect_draft_line = attempt_event.get("reflect_draft_line")
        folded.correction_message_sha256 = attempt_event.get(
            "correction_message_sha256"
        )
        folded.has_pillar2_fields = True
        folded.reconciled = False

    for target_id, events in attempted_by_target.items():
        if target_id not in folded_by_id:
            continue
        folded = folded_by_id[target_id]
        if folded.has_pillar2_fields:
            continue
        if target_id in reverted_targets or target_id in ambiguous_targets:
            continue
        if folded.base.get("reflect_status") != "applied":
            continue
        latest = latest_attempt(events)
        if latest is None:
            continue
        folded.reflect_applied_at = latest.get("attempted_at")
        folded.reflect_target_kind = _read_reflect_target_kind(latest)
        folded.reflect_target_path = latest.get("reflect_target_path")
        folded.reflect_draft_line = latest.get("reflect_draft_line")
        folded.correction_message_sha256 = latest.get("correction_message_sha256")
        folded.has_pillar2_fields = True
        folded.reconciled = True

    return [folded_by_id[correction_id] for correction_id in order], health
