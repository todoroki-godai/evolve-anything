"""corrections.jsonl の位置非依存IDと専用保存境界。"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import persistence


_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _corrections_lock_path(filepath: Path) -> Path:
    resolved = Path(filepath).resolve()
    return resolved.with_name(resolved.name + ".lock")


def fcntl_unsupported_reason() -> Optional[str]:
    return None if persistence._HAVE_FCNTL else (
        "fcntl unavailable: corrections.jsonl の排他書込みは未対応"
    )


def corrections_write_lock(filepath: Path):
    from .file_lock import file_lock

    return file_lock(_corrections_lock_path(Path(filepath)))


def _line_identity(raw_line: str) -> str:
    stripped = raw_line.strip()
    try:
        record = json.loads(stripped)
    except json.JSONDecodeError:
        record = None
    if isinstance(record, dict) and validate_correction_id(record.get("correction_id")):
        return f"id:{record['correction_id']}"
    return f"hash:{hashlib.sha256(stripped.encode('utf-8')).hexdigest()}"


def snapshot_identities(text: str) -> Counter[str]:
    identities: Counter[str] = Counter()
    for line in persistence.split_corrections_lines(text):
        if line.strip():
            identities[_line_identity(line)] += 1
    return identities


class UnexpectedCorrectionLossError(RuntimeError):
    pass


def assert_no_unexpected_content_loss(
    before: Counter[str],
    after: Counter[str],
    *,
    touched_before: Counter[str] = Counter(),
) -> None:
    missing = (before - touched_before) - after
    if missing:
        raise UnexpectedCorrectionLossError(
            f"corrections.jsonl 書換えで {sum(missing.values())} 件の行が"
            f"意図せず消失（touched 宣言に無い identity）: {list(missing.elements())[:5]}"
        )


def atomic_write_text_preserving_mode(path: Path, text: str) -> None:
    from .file_lock import atomic_write_text

    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        pass
    atomic_write_text(path, text)
    if existing_mode is not None:
        path.chmod(existing_mode)


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

    filepath = Path(filepath)
    with corrections_write_lock(filepath):
        result = persistence.append_jsonl(
            filepath,
            record,
            duplicate_check=lambda existing: has_duplicate_id(existing, correction_id),
        )
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", reason=result.reason)


def append_unique_record(store_name: str, record: dict) -> AppendResult:
    """登録ストアへ correction_id 重複拒否つきで追記する専用境界。"""
    if not persistence._HAVE_FCNTL:
        return AppendResult(
            status="unsupported_platform",
            reason="fcntl unavailable: unique append is not supported",
        )

    from .store_write import guard_problem
    import rl_common

    problem = guard_problem(store_name)
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)
    correction_id = record.get("correction_id")
    if not validate_correction_id(correction_id):
        return AppendResult(status="invalid_id")

    rl_common.ensure_data_dir()
    filepath = rl_common.DATA_DIR / store_name
    result = persistence.append_jsonl(
        filepath,
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
