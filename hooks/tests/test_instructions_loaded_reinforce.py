#!/usr/bin/env python3
"""instructions_loaded の SessionStart memory reinforce 本番配線テスト（#18）。

SessionStart で注入される（= アクセスされた）有効 memory を access proxy として
reinforce する。decay 系の決定論ロジックのみで LLM は呼ばない。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

import instructions_loaded
from memory_temporal import parse_memory_temporal


class TestReinforceLoadedMemory:
    """_reinforce_loaded_memory のテスト。"""

    def test_有効memoryがreinforceされる(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "active.md"
        f.write_text(
            "---\nname: active\nsuperseded_at: null\ndecay_days: null\nupdate_count: 0\n---\nbody\n",
            encoding="utf-8",
        )
        instructions_loaded._reinforce_loaded_memory(memory_dir)
        assert parse_memory_temporal(f)["update_count"] == 1

    def test_last_reinforced_atが書かれる(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "active.md"
        f.write_text(
            "---\nname: active\nupdate_count: 0\n---\nbody\n", encoding="utf-8"
        )
        instructions_loaded._reinforce_loaded_memory(memory_dir)
        assert "last_reinforced_at:" in f.read_text(encoding="utf-8")

    def test_stale_memoryはreinforceしない(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "decayed.md"
        f.write_text(
            "---\nname: decayed\nvalid_from: '2020-01-01T00:00:00Z'\ndecay_days: 30\nupdate_count: 0\n---\nbody\n",
            encoding="utf-8",
        )
        instructions_loaded._reinforce_loaded_memory(memory_dir)
        # 注入されない（無視指示が出る）memory は強化しない
        assert parse_memory_temporal(f)["update_count"] == 0

    def test_superseded_memoryはreinforceしない(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "old.md"
        f.write_text(
            "---\nname: old\nsuperseded_at: '2020-01-01T00:00:00Z'\nupdate_count: 0\n---\nbody\n",
            encoding="utf-8",
        )
        instructions_loaded._reinforce_loaded_memory(memory_dir)
        assert parse_memory_temporal(f)["update_count"] == 0

    def test_frontmatterなしはno_op(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "legacy.md"
        f.write_text("# legacy\nbody\n", encoding="utf-8")
        instructions_loaded._reinforce_loaded_memory(memory_dir)
        assert f.read_text(encoding="utf-8") == "# legacy\nbody\n"

    def test_memory_dir非存在は例外なし(self, tmp_path):
        instructions_loaded._reinforce_loaded_memory(tmp_path / "nope")

    def test_memory_temporal_unavailableは例外なし(self, tmp_path, monkeypatch):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "active.md"
        f.write_text("---\nname: active\nupdate_count: 0\n---\nbody\n", encoding="utf-8")
        monkeypatch.setattr(instructions_loaded, "_memory_temporal", None)
        instructions_loaded._reinforce_loaded_memory(memory_dir)
        assert parse_memory_temporal(f)["update_count"] == 0
