"""runtime telemetry の分離表示契約（#268）。"""
from __future__ import annotations

import json
from pathlib import Path

from agent_coordination.runtime_summary import summarize_runtime


def test_summarize_runtime_separates_stores_and_preserves_unknown(tmp_path: Path) -> None:
    (tmp_path / "usage.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in ({"runtime": "claude"}, {"runtime": "codex"}, {})
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "sessions.jsonl").write_text(
        json.dumps({"runtime": "codex"}) + "\nnot-json\n",
        encoding="utf-8",
    )
    result = summarize_runtime(tmp_path)
    assert result["stores"]["usage.jsonl"] == {
        "claude": 1,
        "codex": 1,
        "unknown": 1,
    }
    assert result["stores"]["sessions.jsonl"]["codex"] == 1
    assert result["stores"]["errors.jsonl"]["claude"] == 0
    assert result["totals"] == {"claude": 1, "codex": 2, "unknown": 1}
