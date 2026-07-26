"""usage/sessions/errors の runtime 別件数を決定論集計する（#268）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_JSONL_STORES = ("usage.jsonl", "errors.jsonl")
_RUNTIMES = ("claude", "codex", "unknown")


def _count(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {runtime: 0 for runtime in _RUNTIMES}
    for record in records:
        runtime = record.get("runtime") if isinstance(record, dict) else None
        key = runtime if runtime in {"claude", "codex"} else "unknown"
        counts[key] += 1
    return counts


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def summarize_runtime(data_dir: Path) -> dict[str, Any]:
    data_dir = Path(data_dir).resolve()
    stores: dict[str, dict[str, int]] = {}
    totals = {runtime: 0 for runtime in _RUNTIMES}
    for name in _JSONL_STORES:
        counts = _count(_read_jsonl(data_dir / name))
        stores[name] = counts
        for runtime, count in counts.items():
            totals[runtime] += count

    # sessions のSoRはsessions.db。ingest後にlive JSONLがrotateされるため、
    # session_storeのread-only DB + 未ingest JSONL unionを必ず使う。
    import session_store

    session_counts = _count(session_store.read_session_records(data_dir))
    stores["sessions"] = session_counts
    for runtime, count in session_counts.items():
        totals[runtime] += count
    return {
        "schema_version": 1,
        "data_dir": str(data_dir),
        "session_reader": "session_store.read_session_records",
        "stores": stores,
        "totals": totals,
    }
