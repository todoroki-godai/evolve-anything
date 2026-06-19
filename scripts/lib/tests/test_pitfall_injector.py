"""pitfall_manager/injector.py のユニットテスト。"""
import json
import os
from pathlib import Path
from unittest import mock

import pytest

import pitfall_manager.injector as injector


@pytest.fixture()
def tmp_errors(tmp_path):
    """errors.jsonl を tmp_path 内に作成して DATA_DIR をモック。"""
    errors_file = tmp_path / "errors.jsonl"
    with mock.patch.object(injector, "DATA_DIR", tmp_path):
        yield errors_file


def _write_errors(errors_file: Path, records: list) -> None:
    lines = [json.dumps(r) for r in records]
    errors_file.write_text("\n".join(lines) + "\n")


class TestCountRecentErrors:
    def test_no_errors_file(self, tmp_path):
        with mock.patch.object(injector, "DATA_DIR", tmp_path):
            assert injector.count_recent_errors("sess-1") == 0

    def test_no_matching_session(self, tmp_errors):
        _write_errors(tmp_errors, [
            {"session_id": "other", "tool_name": "Bash"},
            {"session_id": "other2", "tool_name": "Read"},
        ])
        assert injector.count_recent_errors("sess-1") == 0

    def test_counts_matching_session(self, tmp_errors):
        _write_errors(tmp_errors, [
            {"session_id": "sess-1", "tool_name": "Bash"},
            {"session_id": "other", "tool_name": "Read"},
            {"session_id": "sess-1", "tool_name": "Edit"},
        ])
        assert injector.count_recent_errors("sess-1") == 2

    def test_tail_limit(self, tmp_errors):
        records = [{"session_id": "sess-1", "tool_name": "Bash"}] * 10
        records += [{"session_id": "other", "tool_name": "Read"}] * 5
        _write_errors(tmp_errors, records)
        # tail_lines=5 だと最後の 5 行（other のみ）しか見ない
        assert injector.count_recent_errors("sess-1", tail_lines=5) == 0
        # tail_lines=200 なら全部見る
        assert injector.count_recent_errors("sess-1", tail_lines=200) == 10

    def test_malformed_lines_ignored(self, tmp_errors):
        tmp_errors.write_text(
            'not json\n'
            '{"session_id": "sess-1", "tool_name": "Bash"}\n'
        )
        assert injector.count_recent_errors("sess-1") == 1

    def test_empty_file(self, tmp_errors):
        tmp_errors.write_text("")
        assert injector.count_recent_errors("sess-1") == 0


class TestGetPitfallForSkill:
    def test_no_pitfalls_file(self, tmp_path):
        with mock.patch.object(injector, "_plugin_root", tmp_path):
            assert injector.get_pitfall_for_skill("evolve") is None

    def test_returns_active_section(self, tmp_path):
        pitfall_dir = tmp_path / "skills" / "evolve" / "references"
        pitfall_dir.mkdir(parents=True)
        pitfall_file = pitfall_dir / "pitfalls.md"
        pitfall_file.write_text(
            "## Active Pitfalls\n\n### P1: Something\n詳細テキスト\n\n"
            "## Candidate Pitfalls\n\n### C1: Candidate\nshould not appear\n"
        )
        with mock.patch.object(injector, "_plugin_root", tmp_path):
            result = injector.get_pitfall_for_skill("evolve")
        assert result is not None
        assert "P1: Something" in result
        assert "should not appear" not in result

    def test_empty_active_section_returns_none(self, tmp_path):
        pitfall_dir = tmp_path / "skills" / "myskill" / "references"
        pitfall_dir.mkdir(parents=True)
        (pitfall_dir / "pitfalls.md").write_text(
            "## Active Pitfalls\n\n## Candidate Pitfalls\n\n### C1\ntext\n"
        )
        with mock.patch.object(injector, "_plugin_root", tmp_path):
            result = injector.get_pitfall_for_skill("myskill")
        assert result is None

    def test_path_form_skill_name(self, tmp_path):
        """'path/to/evolve' 形式でも末尾ディレクトリ名を使う。"""
        pitfall_dir = tmp_path / "skills" / "evolve" / "references"
        pitfall_dir.mkdir(parents=True)
        (pitfall_dir / "pitfalls.md").write_text(
            "## Active Pitfalls\n\n### P1\ntext\n"
        )
        with mock.patch.object(injector, "_plugin_root", tmp_path):
            result = injector.get_pitfall_for_skill("/home/user/.claude/skills/evolve-anything/evolve")
        assert result is not None
        assert "P1" in result


class TestIsAlreadyInjected:
    def test_not_injected_when_no_file(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            assert injector.is_already_injected("sess-1", "evolve") is False

    def test_not_injected_when_different_skill(self, tmp_path):
        path = tmp_path / "evolve-anything-injected-sess-1.json"
        path.write_text(json.dumps({"injected_skills": ["commit"]}))
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            assert injector.is_already_injected("sess-1", "evolve") is False

    def test_injected_when_skill_present(self, tmp_path):
        path = tmp_path / "evolve-anything-injected-sess-1.json"
        path.write_text(json.dumps({"injected_skills": ["evolve", "commit"]}))
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            assert injector.is_already_injected("sess-1", "evolve") is True

    def test_path_form_normalized(self, tmp_path):
        path = tmp_path / "evolve-anything-injected-sess-1.json"
        path.write_text(json.dumps({"injected_skills": ["evolve"]}))
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            assert injector.is_already_injected("sess-1", "/home/user/skills/evolve") is True


class TestMarkInjected:
    def test_creates_file_on_first_mark(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            injector.mark_injected("sess-1", "evolve")
        path = tmp_path / "evolve-anything-injected-sess-1.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "evolve" in data["injected_skills"]

    def test_appends_to_existing(self, tmp_path):
        path = tmp_path / "evolve-anything-injected-sess-1.json"
        path.write_text(json.dumps({"injected_skills": ["commit"]}))
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            injector.mark_injected("sess-1", "evolve")
        data = json.loads(path.read_text())
        assert "commit" in data["injected_skills"]
        assert "evolve" in data["injected_skills"]

    def test_no_duplicate(self, tmp_path):
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}):
            injector.mark_injected("sess-1", "evolve")
            injector.mark_injected("sess-1", "evolve")
        path = tmp_path / "evolve-anything-injected-sess-1.json"
        data = json.loads(path.read_text())
        assert data["injected_skills"].count("evolve") == 1

    def test_write_failure_silent(self, tmp_path):
        """書き込み失敗時は例外を出さない。"""
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        with mock.patch.dict(os.environ, {"TMPDIR": str(readonly_dir)}):
            injector.mark_injected("sess-1", "evolve")  # 例外なし
        readonly_dir.chmod(0o755)
