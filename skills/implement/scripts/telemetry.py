"""implement スキルのテレメトリ記録モジュール."""

import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from rl_common import store_write  # noqa: E402


def record_usage(
    *,
    project: str,
    tasks_total: int,
    tasks_completed: int,
    mode: str,
    conformance_rate: float,
    lanes: int = 1,
    outcome: str = "success",
) -> dict:
    """usage.jsonl にスキル使用を記録し、書き込んだレコードを返す."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill": "implement",
        "project": project,
        "tasks_total": tasks_total,
        "tasks_completed": tasks_completed,
        "mode": mode,
        "conformance_rate": round(conformance_rate, 2),
        "lanes": lanes,
        "outcome": outcome,
    }
    store_write("usage.jsonl", record)
    return record
