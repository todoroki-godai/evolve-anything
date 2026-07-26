"""runtime telemetry の分離表示契約（#268）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_coordination.runtime_summary import summarize_runtime


def test_summarize_runtime_uses_session_store_and_preserves_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "usage.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in ({"runtime": "claude"}, {"runtime": "codex"}, {})
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[Path] = []
    monkeypatch.setattr(
        "session_store.read_session_records",
        lambda data_dir: calls.append(data_dir)
        or [{"runtime": "codex"}, {"runtime": "codex"}, {}],
    )
    result = summarize_runtime(tmp_path)
    assert result["stores"]["usage.jsonl"] == {
        "claude": 1,
        "codex": 1,
        "unknown": 1,
    }
    assert calls == [tmp_path.resolve()]
    assert result["stores"]["sessions"]["codex"] == 2
    assert result["stores"]["sessions"]["unknown"] == 1
    assert result["stores"]["errors.jsonl"]["claude"] == 0
    assert result["totals"] == {"claude": 1, "codex": 3, "unknown": 2}
    assert result["session_reader"] == "session_store.read_session_records"
