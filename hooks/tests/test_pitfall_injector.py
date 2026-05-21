"""pitfall_injector.py のユニットテスト。"""
import json
import os
from pathlib import Path
from unittest import mock

import pytest

import common
import pitfall_injector
import pitfall_manager.injector as inj_mod


@pytest.fixture()
def tmp_session(tmp_path, patch_data_dir):
    """TMPDIR と DATA_DIR を tmp_path に向けたセッション環境。"""
    with mock.patch.dict(os.environ, {"TMPDIR": str(tmp_path)}), \
         mock.patch.object(inj_mod, "DATA_DIR", patch_data_dir):
        yield tmp_path, patch_data_dir


def _make_event(session_id: str = "sess-pi-001") -> dict:
    return {"session_id": session_id, "message": {"content": "次どうする？"}}


def _write_errors(data_dir: Path, session_id: str, count: int) -> None:
    errors_file = data_dir / "errors.jsonl"
    lines = [json.dumps({"session_id": session_id, "tool_name": "Bash"}) for _ in range(count)]
    errors_file.write_text("\n".join(lines) + "\n")


def _write_last_skill(tmp_path: Path, session_id: str, skill: str) -> None:
    common.write_last_skill(session_id, skill)


def _make_pitfall_file(plugin_root: Path, skill_name: str, text: str) -> None:
    p = plugin_root / "skills" / skill_name / "references"
    p.mkdir(parents=True, exist_ok=True)
    (p / "pitfalls.md").write_text(
        f"## Active Pitfalls\n\n{text}\n\n## Candidate Pitfalls\n"
    )


class TestHandleUserPromptSubmit:
    def test_injects_pitfall_when_threshold_met(self, tmp_session, tmp_path, capsys):
        tmpdir, data_dir = tmp_session
        _write_errors(data_dir, "sess-pi-001", 3)
        _write_last_skill(tmpdir, "sess-pi-001", "evolve")
        plugin_root = tmp_path / "plugin"
        _make_pitfall_file(plugin_root, "evolve", "### P1: something bad\n詳細")
        with mock.patch.dict(os.environ, {
            "TMPDIR": str(tmpdir),
            "CLAUDE_PLUGIN_OPTION_error_preflight_threshold": "3",
        }), mock.patch.object(inj_mod, "_plugin_root", plugin_root):
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-001"))

        out = capsys.readouterr().out
        assert "pitfall-inject: evolve" in out
        assert "P1: something bad" in out

    def test_no_inject_below_threshold(self, tmp_session, tmp_path, capsys):
        tmpdir, data_dir = tmp_session
        _write_errors(data_dir, "sess-pi-002", 2)  # threshold=3 未満
        _write_last_skill(tmpdir, "sess-pi-002", "evolve")
        plugin_root = tmp_path / "plugin"
        _make_pitfall_file(plugin_root, "evolve", "### P1\ntext")
        with mock.patch.dict(os.environ, {
            "TMPDIR": str(tmpdir),
            "CLAUDE_PLUGIN_OPTION_error_preflight_threshold": "3",
        }), mock.patch.object(inj_mod, "_plugin_root", plugin_root):
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-002"))

        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_no_inject_when_no_last_skill(self, tmp_session, tmp_path, capsys):
        tmpdir, data_dir = tmp_session
        _write_errors(data_dir, "sess-pi-003", 5)
        # last_skill を設定しない
        plugin_root = tmp_path / "plugin"
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmpdir)}), \
             mock.patch.object(inj_mod, "_plugin_root", plugin_root):
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-003"))

        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_no_inject_when_no_pitfall_file(self, tmp_session, tmp_path, capsys):
        tmpdir, data_dir = tmp_session
        _write_errors(data_dir, "sess-pi-004", 5)
        _write_last_skill(tmpdir, "sess-pi-004", "no-pitfall-skill")
        plugin_root = tmp_path / "plugin"
        # pitfall ファイルを作らない
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmpdir)}), \
             mock.patch.object(inj_mod, "_plugin_root", plugin_root):
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-004"))

        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_no_duplicate_inject(self, tmp_session, tmp_path, capsys):
        tmpdir, data_dir = tmp_session
        _write_errors(data_dir, "sess-pi-005", 5)
        _write_last_skill(tmpdir, "sess-pi-005", "evolve")
        plugin_root = tmp_path / "plugin"
        _make_pitfall_file(plugin_root, "evolve", "### P1\ntext")
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmpdir)}), \
             mock.patch.object(inj_mod, "_plugin_root", plugin_root):
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-005"))
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-005"))

        out = capsys.readouterr().out
        # 2回目は inject されない
        assert out.count("pitfall-inject: evolve") == 1

    def test_no_inject_when_session_id_empty(self, capsys):
        event = {"session_id": "", "message": {"content": "test"}}
        pitfall_injector.handle_user_prompt_submit(event)
        assert capsys.readouterr().out.strip() == ""

    def test_custom_threshold_via_env(self, tmp_session, tmp_path, capsys):
        tmpdir, data_dir = tmp_session
        _write_errors(data_dir, "sess-pi-006", 5)
        _write_last_skill(tmpdir, "sess-pi-006", "evolve")
        plugin_root = tmp_path / "plugin"
        _make_pitfall_file(plugin_root, "evolve", "### P1\ntext")
        # 閾値を 10 に設定（エラー5件では足りない）
        with mock.patch.dict(os.environ, {
            "TMPDIR": str(tmpdir),
            "CLAUDE_PLUGIN_OPTION_error_preflight_threshold": "10",
        }), mock.patch.object(inj_mod, "_plugin_root", plugin_root):
            pitfall_injector.handle_user_prompt_submit(_make_event("sess-pi-006"))

        assert capsys.readouterr().out.strip() == ""
