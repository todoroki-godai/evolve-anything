"""Physical-line-safe status updates for corrections.jsonl (#588)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from memory_temporal import make_source_correction_id


def _source_id(record: Dict[str, Any]) -> Optional[str]:
    session_id = record.get("session_id")
    timestamp = record.get("timestamp")
    if not session_id or not timestamp:
        return None
    return make_source_correction_id(str(session_id), str(timestamp))


def update_status_at_logical_indices(
    filepath: Path,
    indices: Iterable[int],
    status: str,
) -> int:
    """Update valid JSON records without treating logical indices as physical lines.

    The requested indices refer to the record list produced by the corrections reader,
    which omits blank and malformed lines. Records with a usable identity are selected by
    ``source_correction_id`` and re-identified while walking the physical file. Legacy
    records without that identity fall back to their valid-record ordinal, never the
    physical line number.
    """
    requested = {int(i) for i in indices if int(i) >= 0}
    if not requested or not filepath.exists():
        return 0

    physical_lines = filepath.read_text(encoding="utf-8").splitlines()
    parsed_lines: List[Tuple[int, Dict[str, Any]]] = []
    for physical_index, line in enumerate(physical_lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict):
            parsed_lines.append((physical_index, record))

    selected_source_ids: Set[str] = set()
    legacy_ordinals: Set[int] = set()
    for logical_index in requested:
        if logical_index >= len(parsed_lines):
            continue
        record = parsed_lines[logical_index][1]
        source_id = _source_id(record)
        if source_id is None:
            legacy_ordinals.add(logical_index)
        else:
            selected_source_ids.add(source_id)

    updated = 0
    for logical_index, (physical_index, record) in enumerate(parsed_lines):
        source_id = _source_id(record)
        selected = (
            source_id in selected_source_ids
            if source_id is not None
            else logical_index in legacy_ordinals
        )
        if not selected:
            continue
        record["reflect_status"] = status
        physical_lines[physical_index] = json.dumps(record, ensure_ascii=False)
        updated += 1

    filepath.write_text("\n".join(physical_lines) + "\n", encoding="utf-8")
    return updated
