#!/usr/bin/env python3
"""audit の temporal memory stale 検出テスト。

TDD: Task 4 — audit が stale/superseded memory を WARN・削除候補を検出
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from audit import build_temporal_memory_warnings


class TestBuildTemporalMemoryWarnings:
    """build_temporal_memory_warnings — stale/superseded memory 検出。"""

    def test_empty_dir_returns_empty(self, tmp_path):
        """memory ディレクトリが空 → 警告なし。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        result = build_temporal_memory_warnings(memory_dir)
        assert result == []

    def test_no_frontmatter_skipped(self, tmp_path):
        """frontmatter なし（既存ファイル）→ スキップ、クラッシュなし。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "legacy.md").write_text("# No frontmatter\n", encoding="utf-8")
        result = build_temporal_memory_warnings(memory_dir)
        assert result == []

    def test_decay_null_not_warned(self, tmp_path):
        """decay_days: null → 警告なし。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "ok.md").write_text(
            "---\nname: ok\nvalid_from: '2020-01-01T00:00:00Z'\ndecay_days: null\n---\n",
            encoding="utf-8",
        )
        result = build_temporal_memory_warnings(memory_dir)
        assert result == []

    def test_decay_zero_not_warned(self, tmp_path):
        """decay_days: 0 → 警告なし（null 相当）。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "ok.md").write_text(
            "---\nname: ok\nvalid_from: '2020-01-01T00:00:00Z'\ndecay_days: 0\n---\n",
            encoding="utf-8",
        )
        result = build_temporal_memory_warnings(memory_dir)
        assert result == []

    def test_memory_dir_is_file_returns_empty(self, tmp_path):
        """memory_dir がファイル（ディレクトリではない）→ 空リスト、例外なし。"""
        not_a_dir = tmp_path / "memory.md"
        not_a_dir.write_text("not a directory", encoding="utf-8")
        result = build_temporal_memory_warnings(not_a_dir)
        assert result == []

    def test_stale_by_decay_warns(self, tmp_path):
        """decay_days 超過 → STALE 警告あり。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "old.md").write_text(
            "---\nname: old\nvalid_from: '2020-01-01T00:00:00Z'\ndecay_days: 30\n---\n",
            encoding="utf-8",
        )
        result = build_temporal_memory_warnings(memory_dir)
        assert len(result) == 1
        assert result[0]["file"] == "old.md"
        assert result[0]["reason"] == "stale"

    def test_superseded_warns(self, tmp_path):
        """superseded_at が過去 → SUPERSEDED 警告あり。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "superseded.md").write_text(
            "---\nname: sup\nsuperseded_at: '2020-01-01T00:00:00Z'\n---\n",
            encoding="utf-8",
        )
        result = build_temporal_memory_warnings(memory_dir)
        assert len(result) == 1
        assert result[0]["reason"] == "superseded"

    def test_deletion_candidate_when_sources_reflected(self, tmp_path):
        """source_correction_ids が全て reflected → deletion_candidate=True。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # corrections.jsonl を tmp_path に作る
        corrections_path = tmp_path / "corrections.jsonl"
        import json
        corrections_path.write_text(
            json.dumps({
                "session_id": "sess-abc",
                "timestamp": "2026-01-15T10:23:00.000Z",
                "reflect_status": "applied",  # 実際に書き込まれる値
            }) + "\n",
            encoding="utf-8",
        )
        (memory_dir / "mem.md").write_text(
            "---\nname: mem\n"
            "valid_from: '2020-01-01T00:00:00Z'\ndecay_days: 30\n"
            "source_correction_ids:\n"
            "  - 'sess-abc#2026-01-15T10:23:00.000Z'\n"
            "---\n",
            encoding="utf-8",
        )
        result = build_temporal_memory_warnings(
            memory_dir, corrections_path=corrections_path
        )
        assert len(result) == 1
        assert result[0]["deletion_candidate"] is True

    def test_not_deletion_candidate_when_source_pending(self, tmp_path):
        """source_correction_ids の一部が未 reflect → deletion_candidate=False。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        corrections_path = tmp_path / "corrections.jsonl"
        import json
        corrections_path.write_text(
            json.dumps({
                "session_id": "sess-abc",
                "timestamp": "2026-01-15T10:23:00.000Z",
                "reflect_status": "pending",  # まだ reflect されていない
            }) + "\n",
            encoding="utf-8",
        )
        (memory_dir / "mem.md").write_text(
            "---\nname: mem\n"
            "valid_from: '2020-01-01T00:00:00Z'\ndecay_days: 30\n"
            "source_correction_ids:\n"
            "  - 'sess-abc#2026-01-15T10:23:00.000Z'\n"
            "---\n",
            encoding="utf-8",
        )
        result = build_temporal_memory_warnings(
            memory_dir, corrections_path=corrections_path
        )
        assert len(result) == 1
        assert result[0]["deletion_candidate"] is False
