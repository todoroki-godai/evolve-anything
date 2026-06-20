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


def test_本物のsubagentバーストは閾値警告を出す(patch_data_dir, capsys):
    """#36 の agent_type 空 early-return が burst-guard を盲目化していないことの担保。

    本物（agent_type 付き）の subagent を閾値（既定5）分・同一セッション・distinct agent_id で
    生成したら subagent-guard 警告が stdout に出る。ノイズ除外で burst 検出が死んでいない。
    """
    base = {"agent_type": "general-purpose", "session_id": "burst-1"}
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
        for i in range(5):  # 既定 subagent_warning_threshold=5
            event = dict(base, agent_id=f"real-{i}", last_assistant_message="x")
            subagent_observe.handle_subagent_stop(event)
    out = capsys.readouterr().out
    assert "subagent-guard" in out or "subagent" in out  # 警告 JSON が出る
    # 5件すべて本物なので記録もされる
    assert len(_read_subagents(patch_data_dir)) == 5


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


def test_ID形のagent_typeノイズは記録しない(patch_data_dir):
    """harness が agent_type に ID 形（pure hex / agent_id 形）を渡したノイズを遮断する。

    実観測（kazevolve）で agent_type=pure hex が cost breakdown を汚していた。
    """
    noise_events = [
        {"agent_type": "aab2173eb119c5b91", "agent_id": "aaab2173eb119c5b91-x", "session_id": "s1"},
        {"agent_type": "77037416-f452-4241-a414-4eb497336e71", "agent_id": "n5", "session_id": "s1"},
    ]
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
        for e in noise_events:
            subagent_observe.handle_subagent_stop(e)
    assert _read_subagents(patch_data_dir) == []


def test_カスタムagent名は記録する(patch_data_dir):
    """非 hex 文字を含むカスタム agent 名（build-a1 等）は ID 形と誤判定せず記録する。"""
    with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
        for at in ("build-a1", "gamer-mvp29", "fapo-impl"):
            subagent_observe.handle_subagent_stop(
                {"agent_type": at, "agent_id": f"id-{at}", "session_id": "s1"}
            )
    recs = _read_subagents(patch_data_dir)
    assert sorted(r["agent_type"] for r in recs) == ["build-a1", "fapo-impl", "gamer-mvp29"]
