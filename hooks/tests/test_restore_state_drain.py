"""restore_state の evolve-drain リマインド（#402）。

SessionStart で「前回 emit した提案が apply 済みなのに未 drain」を検出して surface するか、
apply 前 / drain 済みでは沈黙するかを決定論で固定する。undrained_applied は store を読まない
ので hook 文脈でも #358 を踏まない。
"""
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import evolve_decisions as ed  # noqa: E402
import restore_state  # noqa: E402


_BEFORE = "# s\n\n旧。\n"
_AFTER = "# s\n\n新しい手順。\n"


@pytest.fixture
def skill_and_marker(tmp_path, monkeypatch):
    """skill file + その before_sha を持つ marker を testslug に用意する。"""
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "testslug")
    sf = tmp_path / "skills" / "s" / "SKILL.md"
    sf.parent.mkdir(parents=True)
    sf.write_text(_BEFORE, encoding="utf-8")
    ed.write_pending_marker(
        "testslug",
        [{"id": "evdiff_x", "skill_name": "s", "skill_path": str(sf),
          "before_sha": ed._sha256(_BEFORE)}],
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return sf


def test_reminder_silent_when_not_applied(skill_and_marker, capsys):
    restore_state._deliver_evolve_drain_reminder()
    assert capsys.readouterr().out == ""  # 未 apply → 沈黙


def test_reminder_fires_when_applied_undrained(skill_and_marker, capsys):
    skill_and_marker.write_text(_AFTER, encoding="utf-8")  # apply
    restore_state._deliver_evolve_drain_reminder()
    out = capsys.readouterr().out
    assert "rl-evolve --drain" in out
    assert "1 件" in out


def test_reminder_silent_after_drain_clears_marker(skill_and_marker, capsys):
    skill_and_marker.write_text(_AFTER, encoding="utf-8")
    ed.drain_pending(slug="testslug", history_file=skill_and_marker.parent / "hist.jsonl")
    restore_state._deliver_evolve_drain_reminder()
    assert capsys.readouterr().out == ""  # marker 消えた → 沈黙


def test_reminder_silent_when_no_marker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "nope")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    restore_state._deliver_evolve_drain_reminder()
    assert capsys.readouterr().out == ""
