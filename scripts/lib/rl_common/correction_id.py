"""corrections.jsonl の位置非依存IDと専用保存境界。"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import persistence


_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def new_correction_id() -> str:
    return uuid.uuid4().hex


def validate_correction_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))


def has_duplicate_id(records: list[dict], correction_id: str) -> bool:
    return any(
        isinstance(record, dict)
        and validate_correction_id(record.get("correction_id"))
        and record["correction_id"] == correction_id
        for record in records
    )


def find_duplicate_ids(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        correction_id = record.get("correction_id")
        if validate_correction_id(correction_id):
            counts[correction_id] = counts.get(correction_id, 0) + 1
    return {correction_id: count for correction_id, count in counts.items() if count > 1}


@dataclass
class AppendResult:
    status: str
    reason: Optional[str] = None


def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    """correction record の唯一の追記境界。検証と重複拒否を常に実行する。"""
    if not persistence._HAVE_FCNTL:
        return AppendResult(
            status="unsupported_platform",
            reason="fcntl unavailable: unique append is not supported",
        )

    from .store_write import guard_problem

    problem = guard_problem("corrections.jsonl")
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)

    correction_id = record.get("correction_id")
    if not validate_correction_id(correction_id):
        return AppendResult(status="invalid_id")

    result = persistence.append_jsonl(
        Path(filepath),
        record,
        duplicate_check=lambda existing: has_duplicate_id(existing, correction_id),
    )
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", reason=result.reason)


@dataclass
class ResolveResult:
    status: str
    record: Optional[dict] = None
    match_count: int = 0


def resolve_correction_id(records: list[dict], correction_id) -> ResolveResult:
    """correction_id を解決する読取専用関数。"""
    if not validate_correction_id(correction_id):
        return ResolveResult(status="invalid_id")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and validate_correction_id(record.get("correction_id"))
        and record["correction_id"] == correction_id
    ]
    if not matches:
        return ResolveResult(status="not_found")
    if len(matches) > 1:
        return ResolveResult(status="ambiguous", match_count=len(matches))
    return ResolveResult(status="found", record=matches[0], match_count=1)
