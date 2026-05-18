"""post_tool_use_memory hook のユニットテスト。

PostToolUse (Edit/Write) で .claude/*/memory/*.md の update_count を
自動インクリメントする hook のテスト。
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

import post_tool_use_memory


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_memory_file(tmp_path: Path, update_count: int = 0, has_frontmatter: bool = True) -> Path:
    mem_dir = tmp_path / ".claude" / "projects" / "slug" / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    f = mem_dir / "test.md"
    if has_frontmatter:
        f.write_text(f"---\nname: test\nupdate_count: {update_count}\n---\nsome content\n")
    else:
        f.write_text("# Test\nsome content\n")
    return f


def make_event(tool_name: str = "Edit", file_path: str = "") -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "session_id": "test-session",
    }


# ---------------------------------------------------------------------------
# is_memory_file
# ---------------------------------------------------------------------------

class TestIsMemoryFile:
    def test_auto_memory_path_matches(self):
        assert post_tool_use_memory.is_memory_file(
            "/Users/foo/.claude/projects/slug/memory/bar.md"
        )

    def test_project_memory_path_matches(self):
        assert post_tool_use_memory.is_memory_file(
            "/home/user/.claude/memory/bar.md"
        )

    def test_non_memory_parent_no_match(self):
        assert not post_tool_use_memory.is_memory_file(
            "/Users/foo/.claude/projects/slug/rules/bar.md"
        )

    def test_no_claude_in_path_no_match(self):
        assert not post_tool_use_memory.is_memory_file(
            "/Users/foo/projects/slug/memory/bar.md"
        )

    def test_non_md_file_no_match(self):
        assert not post_tool_use_memory.is_memory_file(
            "/Users/foo/.claude/projects/slug/memory/bar.txt"
        )

    def test_empty_path_no_match(self):
        assert not post_tool_use_memory.is_memory_file("")

    def test_memory_md_index_excluded(self):
        """MEMORY.md（インデックスファイル）は対象外。"""
        assert not post_tool_use_memory.is_memory_file(
            "/Users/foo/.claude/projects/slug/memory/MEMORY.md"
        )


# ---------------------------------------------------------------------------
# handle_event
# ---------------------------------------------------------------------------

class TestHandleEvent:
    def test_edit_increments_update_count(self, tmp_path):
        f = _make_memory_file(tmp_path, update_count=1)
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert "update_count: 2" in f.read_text()

    def test_write_increments_update_count(self, tmp_path):
        f = _make_memory_file(tmp_path, update_count=0)
        post_tool_use_memory.handle_event(make_event("Write", str(f)))
        assert "update_count: 1" in f.read_text()

    def test_missing_update_count_key_starts_at_one(self, tmp_path):
        """frontmatter はあるが update_count キーが存在しない場合。"""
        mem_dir = tmp_path / ".claude" / "projects" / "slug" / "memory"
        mem_dir.mkdir(parents=True)
        f = mem_dir / "no_count.md"
        f.write_text("---\nname: test\n---\nsome content\n")
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert "update_count: 1" in f.read_text()

    def test_no_frontmatter_adds_update_count_one(self, tmp_path):
        f = _make_memory_file(tmp_path, has_frontmatter=False)
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert "update_count: 1" in f.read_text()

    def test_bool_update_count_normalized_then_incremented(self, tmp_path):
        """update_count: true は bool → 0 に正規化後 → 1 になる。"""
        mem_dir = tmp_path / ".claude" / "projects" / "slug" / "memory"
        mem_dir.mkdir(parents=True)
        f = mem_dir / "bool_count.md"
        f.write_text("---\nname: test\nupdate_count: true\n---\nsome content\n")
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert "update_count: 1" in f.read_text()

    def test_non_memory_file_not_touched(self, tmp_path):
        f = tmp_path / ".claude" / "projects" / "slug" / "rules" / "bar.md"
        f.parent.mkdir(parents=True)
        original = "---\nname: test\n---\ncontent\n"
        f.write_text(original)
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert f.read_text() == original

    def test_multiedit_increments_update_count(self, tmp_path):
        """MultiEdit ツールも Edit/Write と同様にインクリメント対象。"""
        f = _make_memory_file(tmp_path, update_count=0)
        post_tool_use_memory.handle_event(make_event("MultiEdit", str(f)))
        assert "update_count: 1" in f.read_text()

    def test_bash_tool_memory_file_not_touched(self, tmp_path):
        """Edit/Write/MultiEdit 以外のツールは対象外。"""
        f = _make_memory_file(tmp_path, update_count=0)
        post_tool_use_memory.handle_event({
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi"},
            "session_id": "s",
        })
        assert "update_count: 0" in f.read_text()

    def test_file_not_found_silent_failure(self):
        event = make_event("Edit", "/nonexistent/.claude/projects/x/memory/foo.md")
        post_tool_use_memory.handle_event(event)  # must not raise

    def test_tool_input_missing_file_path_silent_failure(self):
        post_tool_use_memory.handle_event({
            "tool_name": "Edit",
            "tool_input": {},
            "session_id": "s",
        })  # must not raise

    def test_tool_input_none_silent_failure(self):
        post_tool_use_memory.handle_event({
            "tool_name": "Edit",
            "tool_input": None,
            "session_id": "s",
        })  # must not raise

    def test_large_update_count_incremented_correctly(self, tmp_path):
        f = _make_memory_file(tmp_path, update_count=5)
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert "update_count: 6" in f.read_text()

    def test_failed_edit_tool_not_incremented(self, tmp_path):
        """tool_result.is_error=True のとき、ファイルが変わっていないのでカウントしない。"""
        f = _make_memory_file(tmp_path, update_count=1)
        event = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(f)},
            "tool_result": {"is_error": True, "content": "old_string not found"},
            "session_id": "s",
        }
        post_tool_use_memory.handle_event(event)
        assert "update_count: 1" in f.read_text()

    def test_memory_md_not_incremented(self, tmp_path):
        """MEMORY.md はインデックスなので update_count を変更しない。"""
        mem_dir = tmp_path / ".claude" / "projects" / "slug" / "memory"
        mem_dir.mkdir(parents=True)
        f = mem_dir / "MEMORY.md"
        original = "# Memory\n\n- [foo](foo.md)\n"
        f.write_text(original)
        post_tool_use_memory.handle_event(make_event("Edit", str(f)))
        assert f.read_text() == original


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_valid_event_processed(self, tmp_path):
        f = _make_memory_file(tmp_path, update_count=2)
        event = json.dumps(make_event("Edit", str(f)))
        with mock.patch("sys.stdin") as m:
            m.read.return_value = event
            post_tool_use_memory.main()
        assert "update_count: 3" in f.read_text()

    def test_invalid_json_does_not_crash(self):
        with mock.patch("sys.stdin") as m:
            m.read.return_value = "not-json"
            post_tool_use_memory.main()

    def test_empty_stdin_does_not_crash(self):
        with mock.patch("sys.stdin") as m:
            m.read.return_value = ""
            post_tool_use_memory.main()
