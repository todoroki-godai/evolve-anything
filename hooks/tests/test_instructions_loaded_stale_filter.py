#!/usr/bin/env python3
"""instructions_loaded の stale memory フィルタのテスト。

TDD: Task 2 — superseded/stale memory ファイルを stdout に出力
"""
import json
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

import instructions_loaded


def _make_event(session_id: str = "test-session", project_dir: str = "/tmp/proj") -> str:
    return json.dumps({"session_id": session_id, "project_dir": project_dir})


class TestStaleMemoryFilter:
    """_emit_stale_memory_warnings のテスト。"""

    def test_no_memory_dir_no_output(self, tmp_path, capsys):
        """memory ディレクトリが存在しない → 出力なし、例外なし。"""
        instructions_loaded._emit_stale_memory_warnings(tmp_path / "nonexistent")
        captured = capsys.readouterr()
        assert "STALE MEMORY" not in captured.out

    def test_superseded_file_outputs_warning(self, tmp_path, capsys):
        """superseded_at が過去のファイル → STALE MEMORY 出力。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "old_rule.md"
        f.write_text(
            "---\nname: old_rule\nsuperseded_at: '2020-01-01T00:00:00Z'\n---\n# Old\n",
            encoding="utf-8",
        )

        instructions_loaded._emit_stale_memory_warnings(memory_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY: old_rule.md" in captured.out

    def test_stale_by_decay_outputs_warning(self, tmp_path, capsys):
        """decay_days を超過したファイル → STALE MEMORY 出力。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "decayed.md"
        f.write_text(
            "---\nname: decayed\nvalid_from: '2020-01-01T00:00:00Z'\ndecay_days: 30\n---\n# Old\n",
            encoding="utf-8",
        )

        instructions_loaded._emit_stale_memory_warnings(memory_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY: decayed.md" in captured.out

    def test_valid_file_no_warning(self, tmp_path, capsys):
        """有効なファイル（superseded_at: null, decay_days: null）→ 出力なし。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "valid_rule.md"
        f.write_text(
            "---\nname: valid_rule\nsuperseded_at: null\ndecay_days: null\n---\n# Valid\n",
            encoding="utf-8",
        )

        instructions_loaded._emit_stale_memory_warnings(memory_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY" not in captured.out

    def test_no_frontmatter_file_no_warning(self, tmp_path, capsys):
        """frontmatter なし（既存ファイル）→ 出力なし、例外なし（後方互換）。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "legacy.md"
        f.write_text("# Legacy memory\nNo frontmatter here.\n", encoding="utf-8")

        instructions_loaded._emit_stale_memory_warnings(memory_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY" not in captured.out

    def test_memory_temporal_unavailable_no_output(self, tmp_path, capsys):
        """_memory_temporal=None（ImportError 時）→ 出力なし、例外なし。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "stale.md").write_text(
            "---\nname: stale\nsuperseded_at: '2020-01-01T00:00:00Z'\n---\n",
            encoding="utf-8",
        )
        with mock.patch.object(instructions_loaded, "_memory_temporal", None):
            instructions_loaded._emit_stale_memory_warnings(memory_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY" not in captured.out

    def test_memory_dir_is_file_not_dir_no_output(self, tmp_path, capsys):
        """memory_dir がディレクトリではなくファイル → 出力なし、例外なし。"""
        not_a_dir = tmp_path / "memory.md"
        not_a_dir.write_text("not a directory", encoding="utf-8")
        instructions_loaded._emit_stale_memory_warnings(not_a_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY" not in captured.out

    def test_mixed_files_only_stale_warned(self, tmp_path, capsys):
        """有効と無効が混在 → stale のみ出力。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        (memory_dir / "good.md").write_text(
            "---\nname: good\nsuperseded_at: null\n---\n# Good\n", encoding="utf-8"
        )
        (memory_dir / "bad.md").write_text(
            "---\nname: bad\nsuperseded_at: '2020-01-01T00:00:00Z'\n---\n# Bad\n",
            encoding="utf-8",
        )

        instructions_loaded._emit_stale_memory_warnings(memory_dir)
        captured = capsys.readouterr()
        assert "STALE MEMORY: bad.md" in captured.out
        assert "good.md" not in captured.out
