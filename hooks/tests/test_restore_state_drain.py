"""restore_state の SessionStart 自動 drain（#421, #402 リマインドからの昇格）。

#402 はリマインド表示のみだったが、#421 で apply 済み提案を **実際に drain して
optimize_history（fitness 母集団）へ記録する** 自動回収へ昇格した。
- apply 済み → 自動 drain + store 差分（apply 境界をまたぐ E2E）+ marker クリア + 1 行 surface
- 未 apply → 沈黙し store に書かない（将来の apply を取り逃さないよう marker は残す）
- marker 無し → 重い drain 経路に入らない軽量 early-return
- drain 中の例外で hook を落とさない（degrade）
undrained_applied は store を読まず marker の sha 突合だけなので hook 文脈でも #358 を踏まない。
"""
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import evolve_decisions as ed  # noqa: E402
import optimize_history_store as ohs  # noqa: E402
import restore_state  # noqa: E402


_BEFORE = "# s\n\n旧。\n"
_AFTER = "# s\n\n新しい手順。\n"


@pytest.fixture
def skill_and_marker(tmp_path, monkeypatch):
    """skill file + before_sha marker を testslug に用意し、store を tmp に隔離する。"""
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "testslug")
    # optimize_history を tmp に隔離（実環境 ~/.claude/evolve-anything へ書かない）。
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    # restore_state が canonical history_file を解決する経路も tmp に固定する。
    monkeypatch.setattr(
        restore_state, "_resolve_canonical_history_file",
        lambda slug: tmp_path / "optimize_history" / f"{slug}.jsonl",
    )
    sf = tmp_path / "skills" / "s" / "SKILL.md"
    sf.parent.mkdir(parents=True)
    sf.write_text(_BEFORE, encoding="utf-8")
    ed.write_pending_marker(
        "testslug",
        [{"id": "evdiff_x", "skill_name": "s", "skill_path": str(sf),
          "before_sha": ed._sha256(_BEFORE), "pattern": "p", "fitness_func": "skill_quality"}],
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return sf, tmp_path


def test_autodrain_records_to_store_when_applied(skill_and_marker, capsys):
    """apply 済み → 自動 drain で optimize_history に accept が記録され marker が消える（E2E）。"""
    sf, tmp_path = skill_and_marker
    sf.write_text(_AFTER, encoding="utf-8")  # apply（apply 境界をまたぐ）

    history_file = tmp_path / "optimize_history" / "testslug.jsonl"
    assert not history_file.exists()  # before: store 空

    restore_state._deliver_evolve_drain()

    # after: store に accept が 1 件記録された（store 差分を assert）。
    assert history_file.exists()
    body = history_file.read_text(encoding="utf-8")
    assert "evdiff_x_accept" in body
    # marker が消えて自然終息する。
    assert ed.read_pending_marker("testslug") is None
    # 1 行サマリが surface される。
    out = capsys.readouterr().out
    assert "drain" in out.lower()
    assert "accept 1 件" in out


def test_silent_and_no_store_write_when_not_applied(skill_and_marker, capsys):
    """未 apply → drain しない。store に書かず marker も残す（将来の apply を取り逃さない）。"""
    sf, tmp_path = skill_and_marker
    # apply しない（before のまま）。
    restore_state._deliver_evolve_drain()
    history_file = tmp_path / "optimize_history" / "testslug.jsonl"
    assert not history_file.exists()  # store 書き込みなし
    assert capsys.readouterr().out == ""  # 沈黙
    assert ed.read_pending_marker("testslug") is not None  # marker 温存


def test_early_return_when_no_marker(tmp_path, monkeypatch):
    """pending marker が無いとき重い drain 経路に入らない（軽量 early-return）。"""
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")  # 不在
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "nope")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    def _boom(*a, **k):
        raise AssertionError("drain_pending must not be called when no marker exists")

    monkeypatch.setattr(ed, "drain_pending", _boom)
    restore_state._deliver_evolve_drain()  # 例外なく早期 return すること


def test_early_return_when_marker_root_missing(tmp_path, monkeypatch):
    """MARKER_ROOT ディレクトリ自体が無ければ resolve_slug すら呼ばずに沈黙する（最軽量）。"""
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "never-created")

    def _boom_slug(*a, **k):
        raise AssertionError("resolve_slug must not be called when MARKER_ROOT is absent")

    monkeypatch.setattr(ed, "resolve_slug", _boom_slug)
    restore_state._deliver_evolve_drain()


def test_silent_after_drain_clears_marker(skill_and_marker, capsys):
    """drain 済み（marker クリア後）は沈黙する。"""
    sf, tmp_path = skill_and_marker
    sf.write_text(_AFTER, encoding="utf-8")
    ed.drain_pending(slug="testslug", history_file=tmp_path / "optimize_history" / "testslug.jsonl")
    restore_state._deliver_evolve_drain()
    assert capsys.readouterr().out == ""  # marker 消えた → 沈黙


def test_does_not_crash_hook_on_error(skill_and_marker, monkeypatch, capsys):
    """drain 中の例外で hook を落とさない（degrade して stderr に 1 行）。"""
    sf, tmp_path = skill_and_marker
    sf.write_text(_AFTER, encoding="utf-8")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ed, "drain_pending", _boom)
    restore_state._deliver_evolve_drain()  # 例外を投げない
    err = capsys.readouterr().err
    assert "drain" in err.lower()
