"""auto_memory_broker.py のユニットテスト（[ADR-037] Phase 2）。

すべて LLM-free。ingest は responses dict を直接渡すため claude subprocess を呼ばない。
"""
import json
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import auto_memory_broker as amb


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path):
    d = tmp_path / "rl-anything"
    d.mkdir()
    return d


@pytest.fixture
def tmp_memory_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d


def _corrections(count: int = 3, prefix: str = "sess"):
    return [
        {
            "session_id": f"{prefix}-{i:04d}",
            "timestamp": f"2026-05-25T10:0{i}:00Z",
            "original": f"original {i}",
            "corrected": f"corrected {i}",
        }
        for i in range(count)
    ]


def _message_corrections(message: str, count: int = 3):
    return [
        {
            "session_id": f"sess-{i:04d}",
            "timestamp": f"2026-05-25T10:0{i}:00Z",
            "type": "feedback",
            "message": message,
        }
        for i in range(count)
    ]


def _llm_output(summary: str = "test summary") -> str:
    return (
        f"---\nname: auto-test\ndescription: {summary}\n"
        f"metadata:\n  type: feedback\nimportance: medium\n---\n\n{summary}"
    )


# ─── compute_dedup_key ──────────────────────────────────────────────────────


def test_dedup_key_is_deterministic():
    c = _corrections(3)
    assert amb.compute_dedup_key(c) == amb.compute_dedup_key(c)


def test_dedup_key_differs_for_different_input():
    assert amb.compute_dedup_key(_corrections(3)) != amb.compute_dedup_key(_corrections(4))
    assert amb.compute_dedup_key(_corrections(3, "a")) != amb.compute_dedup_key(_corrections(3, "b"))


def test_dedup_key_is_16_hex():
    key = amb.compute_dedup_key(_corrections(2))
    assert re.match(r"^[0-9a-f]{16}$", key)


def test_dedup_key_only_uses_session_and_timestamp():
    """original/corrected が違っても session_id+timestamp が同じなら同 key。"""
    c1 = [{"session_id": "s", "timestamp": "t", "original": "x"}]
    c2 = [{"session_id": "s", "timestamp": "t", "original": "y"}]
    assert amb.compute_dedup_key(c1) == amb.compute_dedup_key(c2)


# ─── enqueue / read_queue ───────────────────────────────────────────────────


def test_enqueue_new_returns_true_and_writes_record(tmp_data_dir):
    assert amb.enqueue(_corrections(3), "myslug", tmp_data_dir) is True
    path = amb.queue_path_for("myslug", tmp_data_dir)
    assert path.exists()
    records = amb.read_queue("myslug", tmp_data_dir)
    assert len(records) == 1
    rec = records[0]
    assert rec["slug"] == "myslug"
    assert rec["dedup_key"] == amb.compute_dedup_key(_corrections(3))
    assert len(rec["corrections"]) == 3
    assert "enqueued_at" in rec


def test_enqueue_duplicate_returns_false_and_stays_one(tmp_data_dir):
    assert amb.enqueue(_corrections(3), "slug", tmp_data_dir) is True
    assert amb.enqueue(_corrections(3), "slug", tmp_data_dir) is False
    assert len(amb.read_queue("slug", tmp_data_dir)) == 1


def test_enqueue_empty_returns_false(tmp_data_dir):
    assert amb.enqueue([], "slug", tmp_data_dir) is False
    assert amb.read_queue("slug", tmp_data_dir) == []


def test_enqueue_pj_scope_isolated(tmp_data_dir):
    """別 slug は別ファイル。"""
    amb.enqueue(_corrections(3), "slug-a", tmp_data_dir)
    amb.enqueue(_corrections(3), "slug-b", tmp_data_dir)
    assert amb.queue_path_for("slug-a", tmp_data_dir) != amb.queue_path_for("slug-b", tmp_data_dir)
    assert len(amb.read_queue("slug-a", tmp_data_dir)) == 1
    assert len(amb.read_queue("slug-b", tmp_data_dir)) == 1


def test_enqueue_different_windows_both_kept(tmp_data_dir):
    amb.enqueue(_corrections(3), "slug", tmp_data_dir)
    amb.enqueue(_corrections(4), "slug", tmp_data_dir)
    assert len(amb.read_queue("slug", tmp_data_dir)) == 2


def test_read_queue_dedups_appended_duplicates(tmp_data_dir):
    """append race で同 key が複数行入っても read_queue は1件に collapse する。"""
    path = amb.queue_path_for("slug", tmp_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"dedup_key": "k1", "slug": "slug", "corrections": _corrections(2), "enqueued_at": "t"}
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
        f.write(json.dumps(rec) + "\n")
    assert len(amb.read_queue("slug", tmp_data_dir)) == 1


def test_read_queue_missing_returns_empty(tmp_data_dir):
    assert amb.read_queue("nope", tmp_data_dir) == []


def test_read_queue_skips_corrupt_and_keyless(tmp_data_dir):
    path = amb.queue_path_for("slug", tmp_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write("not json\n")
        f.write(json.dumps({"no_key": True}) + "\n")
        f.write(json.dumps({"dedup_key": "k", "corrections": []}) + "\n")
    recs = amb.read_queue("slug", tmp_data_dir)
    assert len(recs) == 1
    assert recs[0]["dedup_key"] == "k"


# ─── emit_memory_requests ───────────────────────────────────────────────────


def test_emit_empty_records():
    assert amb.emit_memory_requests([]) == {"requests": []}


def test_emit_builds_requests_per_record():
    records = [
        {"dedup_key": "k1", "slug": "s", "corrections": _corrections(2)},
        {"dedup_key": "k2", "slug": "s", "corrections": _corrections(3)},
    ]
    out = amb.emit_memory_requests(records)
    assert len(out["requests"]) == 2
    ids = [r["id"] for r in out["requests"]]
    assert ids == ["k1", "k2"]
    for r in out["requests"]:
        assert isinstance(r["prompt"], str) and r["prompt"]
        assert "corrections" in r["prompt"]


def test_emit_skips_keyless_records():
    records = [{"slug": "s", "corrections": _corrections(2)}]
    assert amb.emit_memory_requests(records) == {"requests": []}


# ─── clear_queue_entries ────────────────────────────────────────────────────


def test_clear_queue_entries_removes_consumed(tmp_data_dir):
    amb.enqueue(_corrections(3), "slug", tmp_data_dir)
    amb.enqueue(_corrections(4), "slug", tmp_data_dir)
    k1 = amb.compute_dedup_key(_corrections(3))
    amb.clear_queue_entries("slug", tmp_data_dir, {k1})
    remaining = amb.read_queue("slug", tmp_data_dir)
    assert len(remaining) == 1
    assert remaining[0]["dedup_key"] == amb.compute_dedup_key(_corrections(4))


def test_clear_queue_entries_all_leaves_empty(tmp_data_dir):
    amb.enqueue(_corrections(3), "slug", tmp_data_dir)
    k = amb.compute_dedup_key(_corrections(3))
    amb.clear_queue_entries("slug", tmp_data_dir, {k})
    assert amb.read_queue("slug", tmp_data_dir) == []


def test_clear_queue_entries_missing_file_noop(tmp_data_dir):
    amb.clear_queue_entries("nope", tmp_data_dir, {"x"})  # should not raise


# ─── ingest_memory_results ──────────────────────────────────────────────────


def test_ingest_writes_md_and_index(tmp_data_dir, tmp_memory_dir):
    corrections = _corrections(3)
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    responses = {key: _llm_output("ingest test entry")}

    memory_md = tmp_memory_dir.parent / "MEMORY.md"
    memory_md.write_text("# MEMORY\n\n## 変更履歴\n\n")

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        result = amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, memory_md, tmp_data_dir,
        )

    assert result["stored"] == 1
    assert result["blocked"] == 0
    assert result["skipped"] == 0

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 1
    assert "ingest test entry" in md_files[0].read_text()

    index_lines = [l for l in memory_md.read_text().splitlines() if l.startswith("- [auto_")]
    assert len(index_lines) == 1

    # 処理後キューが空になる
    assert amb.read_queue("slug", tmp_data_dir) == []


def test_ingest_filename_format(tmp_data_dir, tmp_memory_dir):
    corrections = _corrections(2)
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    responses = {key: _llm_output("fmt")}

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
        )

    md_files = list(tmp_memory_dir.glob("auto_*.md"))
    assert len(md_files) == 1
    assert re.match(r"^auto_\d{8}_\d{6}_[0-9a-f]+\.md$", md_files[0].name)


def test_ingest_required_frontmatter(tmp_data_dir, tmp_memory_dir):
    corrections = _corrections(2)
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    body = "---\nname: test-entry\ndescription: A test memory\nmetadata:\n  type: feedback\nimportance: medium\n---\n\nSome content."
    responses = {key: body}

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
        )

    content = list(tmp_memory_dir.glob("auto_*.md"))[0].read_text()
    assert "name:" in content
    assert "description:" in content


def test_ingest_empty_response_skipped_and_kept_in_queue(tmp_data_dir, tmp_memory_dir):
    corrections = _corrections(2)
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    responses = {key: ""}  # 空

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        result = amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
        )

    assert result["stored"] == 0
    assert result["skipped"] == 1
    assert list(tmp_memory_dir.glob("auto_*.md")) == []
    # 空応答はキューに残す（次 drain で再試行）
    assert len(amb.read_queue("slug", tmp_data_dir)) == 1


def test_ingest_missing_response_skipped(tmp_data_dir, tmp_memory_dir):
    corrections = _corrections(2)
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    responses = {}  # missing

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        result = amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
        )

    assert result["stored"] == 0
    assert result["skipped"] == 1
    assert len(amb.read_queue("slug", tmp_data_dir)) == 1


def test_ingest_belief_block_skips_write(tmp_data_dir, tmp_memory_dir):
    """生成後ゲート: 要約がソースを落としていれば書込なし + belief_blocks.jsonl 記録。"""
    corrections = _message_corrections(
        "always use absolute paths in bash commands never cd into directories"
    )
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    # ソースと無関係な要約 → retention ≈ 0 → block
    responses = {key: _llm_output(
        "completely different topic about pytest fixtures and mocking strategies today"
    )}

    memory_md = tmp_memory_dir.parent / "MEMORY.md"
    memory_md.write_text("# MEMORY\n\n## 変更履歴\n\n")

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "0"}):
        result = amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, memory_md, tmp_data_dir,
        )

    assert result["blocked"] == 1
    assert result["stored"] == 0
    assert list(tmp_memory_dir.glob("auto_*.md")) == []
    index_lines = [l for l in memory_md.read_text().splitlines() if l.startswith("- [auto_")]
    assert index_lines == []

    blocks_file = tmp_data_dir / "belief_blocks.jsonl"
    assert blocks_file.exists()
    assert len(blocks_file.read_text().strip().splitlines()) == 1

    # block は処理済み → キューから消化される
    assert amb.read_queue("slug", tmp_data_dir) == []


def test_ingest_belief_pass_writes(tmp_data_dir, tmp_memory_dir):
    """生成後ゲート: 要約がソースを保持していれば書き込む。"""
    corrections = _message_corrections(
        "always use absolute paths in bash commands never cd into directories"
    )
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    responses = {key: _llm_output(
        "Always use absolute paths in bash commands. Never cd into directories."
    )}

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "0"}):
        result = amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
        )

    assert result["stored"] == 1
    assert len(list(tmp_memory_dir.glob("auto_*.md"))) == 1
    assert not (tmp_data_dir / "belief_blocks.jsonl").exists()


def test_ingest_archive_when_memory_over_limit(tmp_data_dir, tmp_memory_dir):
    """MEMORY.md が 200 行超 → archive.md に古いエントリを移す。"""
    corrections = _corrections(3)
    amb.enqueue(corrections, "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    key = records[0]["dedup_key"]
    responses = {key: _llm_output("archive trigger")}

    memory_md = tmp_memory_dir.parent / "MEMORY.md"
    lines = ["# MEMORY", "", "## 変更履歴", ""]
    for i in range(200):
        lines.append(f"- [old_{i:03d}](old_{i:03d}.md) — old summary {i}")
    memory_md.write_text("\n".join(lines) + "\n")

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, memory_md, tmp_data_dir,
        )

    new_line_count = memory_md.read_text().count("\n") + 1
    assert new_line_count <= 200
    archive_path = memory_md.parent / "archive.md"
    assert archive_path.exists()
    assert "old_" in archive_path.read_text()


def test_ingest_empty_records_noop(tmp_data_dir, tmp_memory_dir):
    result = amb.ingest_memory_results(
        [], [], {}, tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
    )
    assert result == {"stored": 0, "blocked": 0, "skipped": 0, "entries": []}


def test_ingest_multiple_records_partial(tmp_data_dir, tmp_memory_dir):
    """複数 record: 1件 stored, 1件 skipped（空応答）→ stored だけキューから消える。"""
    amb.enqueue(_corrections(3), "slug", tmp_data_dir)
    amb.enqueue(_corrections(4), "slug", tmp_data_dir)
    records = amb.read_queue("slug", tmp_data_dir)
    emit = amb.emit_memory_requests(records)
    k_stored = records[0]["dedup_key"]
    k_skipped = records[1]["dedup_key"]
    responses = {k_stored: _llm_output("kept"), k_skipped: ""}

    with mock.patch.dict("os.environ", {"RL_GATING_DISABLED": "1"}):
        result = amb.ingest_memory_results(
            records, emit["requests"], responses,
            tmp_memory_dir, tmp_memory_dir.parent / "MEMORY.md", tmp_data_dir,
        )

    assert result["stored"] == 1
    assert result["skipped"] == 1
    remaining = amb.read_queue("slug", tmp_data_dir)
    assert len(remaining) == 1
    assert remaining[0]["dedup_key"] == k_skipped
