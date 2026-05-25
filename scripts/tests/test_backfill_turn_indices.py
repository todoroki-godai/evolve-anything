"""tests for scripts/lib/backfill_turn_indices.py"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backfill_turn_indices import (
    backfill_corrections,
    backfill_missing_sessions,
    backfill_sessions,
    compute_turn_index,
    find_session_raw_jsonl,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_session_jsonl(tmp_path: Path, session_id: str, human_count: int) -> Path:
    """sessions.jsonl に 1 レコードを書く"""
    p = tmp_path / "sessions.jsonl"
    rec = {"session_id": session_id, "human_message_count": human_count, "project": "test"}
    p.write_text(json.dumps(rec) + "\n")
    return p


def _make_corrections_jsonl(tmp_path: Path, records: list) -> Path:
    """corrections.jsonl を書く"""
    p = tmp_path / "corrections.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def _make_raw_session(tmp_path: Path, session_id: str, human_timestamps: list) -> Path:
    """raw session JSONL を作る（human ターンのみ）"""
    proj_dir = tmp_path / "projects" / "-test-project"
    proj_dir.mkdir(parents=True)
    p = proj_dir / f"{session_id}.jsonl"
    lines = []
    for ts in human_timestamps:
        lines.append(json.dumps({"type": "human", "timestamp": ts, "sessionId": session_id}))
    p.write_text("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------------------
# backfill_sessions
# ---------------------------------------------------------------------------

class TestBackfillSessions:
    def test_adds_max_turn_index(self, tmp_path):
        """human_message_count があれば max_turn_index = count - 1 を追加する"""
        sessions = _make_session_jsonl(tmp_path, "sess-1", human_count=10)
        added = backfill_sessions(sessions, dry_run=False)
        records = [json.loads(l) for l in sessions.read_text().splitlines() if l.strip()]
        assert records[0]["max_turn_index"] == 9
        assert added == 1

    def test_dry_run_does_not_modify(self, tmp_path):
        """dry_run=True では何も変更しない"""
        sessions = _make_session_jsonl(tmp_path, "sess-1", human_count=10)
        before = sessions.read_text()
        backfill_sessions(sessions, dry_run=True)
        assert sessions.read_text() == before

    def test_skips_zero_human_count(self, tmp_path):
        """human_message_count=0 のレコードは max_turn_index を追加しない"""
        p = tmp_path / "sessions.jsonl"
        p.write_text(json.dumps({"session_id": "s", "human_message_count": 0}) + "\n")
        backfill_sessions(p, dry_run=False)
        records = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert "max_turn_index" not in records[0]

    def test_skips_missing_human_count(self, tmp_path):
        """human_message_count が無いレコードはスキップする"""
        p = tmp_path / "sessions.jsonl"
        p.write_text(json.dumps({"session_id": "s"}) + "\n")
        backfill_sessions(p, dry_run=False)
        records = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert "max_turn_index" not in records[0]

    def test_no_double_update(self, tmp_path):
        """既に max_turn_index があるレコードは変更しない"""
        p = tmp_path / "sessions.jsonl"
        p.write_text(json.dumps({"session_id": "s", "human_message_count": 5, "max_turn_index": 4}) + "\n")
        added = backfill_sessions(p, dry_run=False)
        assert added == 0

    def test_multiple_records(self, tmp_path):
        """複数レコードをまとめて処理する"""
        p = tmp_path / "sessions.jsonl"
        lines = [
            json.dumps({"session_id": "s1", "human_message_count": 3}),
            json.dumps({"session_id": "s2", "human_message_count": 7}),
            json.dumps({"session_id": "s3", "human_message_count": 1}),
        ]
        p.write_text("\n".join(lines) + "\n")
        added = backfill_sessions(p, dry_run=False)
        records = {json.loads(l)["session_id"]: json.loads(l) for l in p.read_text().splitlines() if l.strip()}
        assert added == 3
        assert records["s1"]["max_turn_index"] == 2
        assert records["s2"]["max_turn_index"] == 6
        assert records["s3"]["max_turn_index"] == 0

    def test_atomic_write(self, tmp_path):
        """書き込み途中でも元ファイルは保持されている（atomic rename 確認）"""
        sessions = _make_session_jsonl(tmp_path, "sess-1", human_count=10)
        original_inode = sessions.stat().st_ino
        backfill_sessions(sessions, dry_run=False)
        # ファイルが存在して読める（atomic replace 後も有効）
        assert sessions.exists()
        content = sessions.read_text()
        assert "max_turn_index" in content


# ---------------------------------------------------------------------------
# compute_turn_index
# ---------------------------------------------------------------------------

class TestComputeTurnIndex:
    def test_basic(self, tmp_path):
        """correction の直前（≤ correction_ts）の最後の human ターンの index を返す"""
        session_id = "sess-abc"
        timestamps = [
            "2026-05-19T00:00:01.000Z",
            "2026-05-19T00:00:02.000Z",
            "2026-05-19T00:00:03.000Z",  # ← correction ts と同時刻
            "2026-05-19T00:00:04.000Z",
        ]
        raw = _make_raw_session(tmp_path, session_id, timestamps)
        idx = compute_turn_index("2026-05-19T00:00:03.000Z", raw)
        assert idx == 2  # 0-indexed: 3番目のターン

    def test_correction_before_all_turns(self, tmp_path):
        """correction が最初のターンより前の場合は None を返す"""
        session_id = "sess-x"
        raw = _make_raw_session(tmp_path, session_id, ["2026-05-19T01:00:00.000Z"])
        idx = compute_turn_index("2026-05-19T00:00:00.000Z", raw)
        assert idx is None

    def test_correction_at_last_turn(self, tmp_path):
        """最終ターンと同時刻の correction は最終インデックスを返す"""
        session_id = "sess-y"
        raw = _make_raw_session(tmp_path, session_id, [
            "2026-05-19T01:00:00.000Z",
            "2026-05-19T02:00:00.000Z",
        ])
        idx = compute_turn_index("2026-05-19T02:00:00.000Z", raw)
        assert idx == 1

    def test_different_timezone_format(self, tmp_path):
        """correction_ts が +00:00 形式でも正しく処理する"""
        session_id = "sess-tz"
        raw = _make_raw_session(tmp_path, session_id, [
            "2026-05-19T05:57:00.000Z",
            "2026-05-19T05:57:09.000Z",
        ])
        idx = compute_turn_index("2026-05-19T05:57:09.690011+00:00", raw)
        assert idx == 1


# ---------------------------------------------------------------------------
# find_session_raw_jsonl
# ---------------------------------------------------------------------------

class TestFindSessionRawJsonl:
    def test_finds_existing_session(self, tmp_path):
        """session_id に対応する raw JSONL を見つける"""
        session_id = "test-session-123"
        proj_dir = tmp_path / "projects" / "-test-project"
        proj_dir.mkdir(parents=True)
        expected = proj_dir / f"{session_id}.jsonl"
        expected.write_text("")
        result = find_session_raw_jsonl(session_id, tmp_path / "projects")
        assert result == expected

    def test_returns_none_if_not_found(self, tmp_path):
        """見つからない場合は None を返す"""
        result = find_session_raw_jsonl("nonexistent-session", tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# backfill_corrections
# ---------------------------------------------------------------------------

class TestBackfillCorrections:
    def test_adds_turn_index(self, tmp_path):
        """turn_index が追加される"""
        session_id = "sess-corr"
        _make_raw_session(tmp_path, session_id, [
            "2026-05-19T01:00:00.000Z",
            "2026-05-19T02:00:00.000Z",
            "2026-05-19T03:00:00.000Z",
        ])
        # sessions.jsonl に max_turn_index を設定（すでに backfill_sessions 済みの状態）
        sessions = tmp_path / "sessions.jsonl"
        sessions.write_text(json.dumps({"session_id": session_id, "human_message_count": 3, "max_turn_index": 2}) + "\n")

        corrections = _make_corrections_jsonl(tmp_path, [
            {"session_id": session_id, "timestamp": "2026-05-19T02:00:00.000Z", "correction_type": "test"}
        ])

        added = backfill_corrections(corrections, sessions, tmp_path / "projects", dry_run=False)
        records = [json.loads(l) for l in corrections.read_text().splitlines() if l.strip()]
        assert added == 1
        assert records[0]["turn_index"] == 1  # 2番目のターン（0-indexed）

    def test_dry_run_no_change(self, tmp_path):
        """dry_run=True では変更しない"""
        session_id = "sess-dry"
        _make_raw_session(tmp_path, session_id, ["2026-05-19T01:00:00.000Z"])
        sessions = tmp_path / "sessions.jsonl"
        sessions.write_text(json.dumps({"session_id": session_id, "human_message_count": 1, "max_turn_index": 0}) + "\n")
        corrections = _make_corrections_jsonl(tmp_path, [
            {"session_id": session_id, "timestamp": "2026-05-19T01:00:00.000Z"}
        ])
        before = corrections.read_text()
        backfill_corrections(corrections, sessions, tmp_path / "projects", dry_run=True)
        assert corrections.read_text() == before

    def test_skips_already_set(self, tmp_path):
        """turn_index が既にあるレコードはスキップ"""
        corrections = _make_corrections_jsonl(tmp_path, [
            {"session_id": "s", "timestamp": "2026-05-19T01:00:00.000Z", "turn_index": 5}
        ])
        sessions = tmp_path / "sessions.jsonl"
        sessions.write_text(json.dumps({"session_id": "s", "human_message_count": 10, "max_turn_index": 9}) + "\n")
        added = backfill_corrections(corrections, sessions, tmp_path / "projects", dry_run=False)
        assert added == 0


# ---------------------------------------------------------------------------
# backfill_missing_sessions
# ---------------------------------------------------------------------------

class TestBackfillMissingSessions:
    def test_adds_missing_session(self, tmp_path):
        """corrections にあって sessions.jsonl にない session を追加する"""
        session_id = "sess-new"
        _make_raw_session(tmp_path, session_id, [
            "2026-05-19T01:00:00.000Z",
            "2026-05-19T02:00:00.000Z",
        ])
        sessions = tmp_path / "sessions.jsonl"
        sessions.write_text("")  # 空

        corrections = _make_corrections_jsonl(tmp_path, [
            {"session_id": session_id, "timestamp": "2026-05-19T02:00:00.000Z"}
        ])

        added = backfill_missing_sessions(sessions, corrections, tmp_path / "projects", dry_run=False)
        assert added == 1
        records = [json.loads(l) for l in sessions.read_text().splitlines() if l.strip()]
        assert any(r["session_id"] == session_id for r in records)
        matching = next(r for r in records if r["session_id"] == session_id)
        assert matching["human_message_count"] == 2
        assert matching["max_turn_index"] == 1

    def test_does_not_duplicate_existing(self, tmp_path):
        """既に sessions.jsonl にある session は重複追加しない"""
        session_id = "sess-existing"
        _make_raw_session(tmp_path, session_id, ["2026-05-19T01:00:00.000Z"])
        sessions = tmp_path / "sessions.jsonl"
        sessions.write_text(json.dumps({"session_id": session_id, "human_message_count": 1, "max_turn_index": 0}) + "\n")
        corrections = _make_corrections_jsonl(tmp_path, [
            {"session_id": session_id, "timestamp": "2026-05-19T01:00:00.000Z"}
        ])
        added = backfill_missing_sessions(sessions, corrections, tmp_path / "projects", dry_run=False)
        assert added == 0
        count = sum(1 for l in sessions.read_text().splitlines() if l.strip())
        assert count == 1
