"""Physical-line-safe status updates for corrections.jsonl (#588)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Tuple


def is_valid_correction_record(record: Any) -> bool:
    """Return whether decoded JSON satisfies the corrections reader contract."""
    return isinstance(record, dict)


def update_status_at_logical_indices(
    filepath: Path,
    indices: Iterable[int],
    status: str,
) -> int:
    """Update valid JSON records without treating logical indices as physical lines.

    The requested indices refer to the record list produced by the corrections reader,
    which omits blank, malformed, and non-object JSON lines. Each requested ordinal
    selects exactly one valid record, including when source identities are duplicated.
    """
    requested = {int(i) for i in indices if int(i) >= 0}
    if not requested or not filepath.exists():
        return 0

    physical_lines = filepath.read_text(encoding="utf-8").splitlines()
    parsed_lines: List[Tuple[int, dict[str, Any]]] = []
    for physical_index, line in enumerate(physical_lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if is_valid_correction_record(record):
            parsed_lines.append((physical_index, record))

    updated = 0
    for logical_index, (physical_index, record) in enumerate(parsed_lines):
        if logical_index not in requested:
            continue
        record["reflect_status"] = status
        physical_lines[physical_index] = json.dumps(record, ensure_ascii=False)
        updated += 1

    if updated:
        filepath.write_text("\n".join(physical_lines) + "\n", encoding="utf-8")
    return updated
