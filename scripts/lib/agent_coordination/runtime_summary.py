"""usage/sessions/errors の runtime 別件数を決定論集計する（#268）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STORES = ("usage.jsonl", "sessions.jsonl", "errors.jsonl")
_RUNTIMES = ("claude", "codex", "unknown")


def summarize_runtime(data_dir: Path) -> dict[str, Any]:
    data_dir = Path(data_dir).resolve()
    stores: dict[str, dict[str, int]] = {}
    totals = {runtime: 0 for runtime in _RUNTIMES}
    for name in _STORES:
        counts = {runtime: 0 for runtime in _RUNTIMES}
        path = data_dir / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                runtime = record.get("runtime") if isinstance(record, dict) else None
                key = runtime if runtime in {"claude", "codex"} else "unknown"
                counts[key] += 1
                totals[key] += 1
        stores[name] = counts
    return {
        "schema_version": 1,
        "data_dir": str(data_dir),
        "stores": stores,
        "totals": totals,
    }
