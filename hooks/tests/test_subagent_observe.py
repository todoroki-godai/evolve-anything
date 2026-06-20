"""subagent_observe.py の writer 汚染遮断テスト（#36）。

SubagentStop は本物の Task agent 以外（compaction 要約・メインセッション Stop 等）でも
発火し agent_type が空になる。本物の subagent は必ず agent_type を持つので空は記録しない。
"""
import json
import os
from unittest import mock

import subagent_observe


def _read_subagents(data_dir):
    p = data_dir / "subagents.jsonl"
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_本物のTask_agentは記録する(patch_data_dir):
    event = {
        "agent_type": "general-purpose",
        "agent_id": "a-real-001",
        "last_assistant_message": "done",
        "session_id": "s1",
    }
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
        subagent_observe.handle_subagent_stop(event)
    recs = _read_subagents(patch_data_dir)
    assert len(recs) == 1
    assert recs[0]["agent_type"] == "general-purpose"


def test_agent_type空のノイズは記録しない(patch_data_dir):
    # compaction 要約相当（agent_type 空・<analysis> 本文）
    noise_events = [
        {"agent_type": "", "agent_id": "n1", "last_assistant_message": "<analysis>...", "session_id": "s1"},
        {"agent_id": "n2", "last_assistant_message": "You've hit your limit", "session_id": "s1"},  # agent_type 欠損
        {"agent_type": "   ", "agent_id": "n3", "last_assistant_message": "x", "session_id": "s1"},  # 空白のみ
    ]
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
        for e in noise_events:
            subagent_observe.handle_subagent_stop(e)
    recs = _read_subagents(patch_data_dir)
    assert recs == []  # 1件も記録されない
