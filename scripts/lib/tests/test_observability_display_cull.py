"""#379 Step 2「表示淘汰」— collect_observability の gate + 透明化 1 行の契約テスト。

淘汰は表示のみ（builder 自体は _OBSERVABILITY_BUILDERS に登録されたまま）。
本テストは builder registry を monkeypatch で差し替え、実データや実 PJ 状態に依存せず
gate ロジックそのものを検証する（learning_synthetic_fixture_false_confidence 対策として、
report.py 経由の統合テストは別途 test_report_display_cull.py で実 generate_report を通す）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import observability as obs_mod  # noqa: E402


def _builder(lines):
    def _fn(_project_dir):
        return list(lines)

    return _fn


def test_culled_key_excluded_by_default(monkeypatch, tmp_path: Path) -> None:
    fake_builders = [
        ("culled_key", _builder(["## Culled", "content", ""])),
        ("kept_key", _builder(["## Kept", "content", ""])),
    ]
    monkeypatch.setattr(obs_mod, "_OBSERVABILITY_BUILDERS", fake_builders)
    monkeypatch.setattr(
        obs_mod.shrink_freeze, "CULLED_OBSERVABILITY_SECTIONS", frozenset({"culled_key"})
    )
    monkeypatch.delenv("EVOLVE_SHOW_CULLED", raising=False)

    result = obs_mod.collect_observability(tmp_path)

    assert "culled_key" not in result
    assert "kept_key" in result


def test_culled_key_builder_not_called(monkeypatch, tmp_path: Path) -> None:
    """淘汰時は builder 自体を呼ばない（skip はループ内の呼び出し前判定）。"""
    calls: list = []

    def _tracking_builder(project_dir):
        calls.append(project_dir)
        return ["## Culled", "content", ""]

    fake_builders = [("culled_key", _tracking_builder)]
    monkeypatch.setattr(obs_mod, "_OBSERVABILITY_BUILDERS", fake_builders)
    monkeypatch.setattr(
        obs_mod.shrink_freeze, "CULLED_OBSERVABILITY_SECTIONS", frozenset({"culled_key"})
    )
    monkeypatch.delenv("EVOLVE_SHOW_CULLED", raising=False)

    obs_mod.collect_observability(tmp_path)

    assert calls == []


def test_env_escape_hatch_shows_all(monkeypatch, tmp_path: Path) -> None:
    fake_builders = [
        ("culled_key", _builder(["## Culled", "content", ""])),
        ("kept_key", _builder(["## Kept", "content", ""])),
    ]
    monkeypatch.setattr(obs_mod, "_OBSERVABILITY_BUILDERS", fake_builders)
    monkeypatch.setattr(
        obs_mod.shrink_freeze, "CULLED_OBSERVABILITY_SECTIONS", frozenset({"culled_key"})
    )
    monkeypatch.setenv("EVOLVE_SHOW_CULLED", "1")

    result = obs_mod.collect_observability(tmp_path)

    assert "culled_key" in result
    assert "kept_key" in result
    # エスケープハッチ中は「淘汰した」事実自体が無いので display_cull も出ない。
    assert "display_cull" not in result


def test_display_cull_notice_is_single_line_with_count(monkeypatch, tmp_path: Path) -> None:
    fake_builders = [
        ("culled_a", _builder(["## A", ""])),
        ("culled_b", _builder(["## B", ""])),
        ("kept", _builder(["## Kept", ""])),
    ]
    monkeypatch.setattr(obs_mod, "_OBSERVABILITY_BUILDERS", fake_builders)
    monkeypatch.setattr(
        obs_mod.shrink_freeze,
        "CULLED_OBSERVABILITY_SECTIONS",
        frozenset({"culled_a", "culled_b"}),
    )
    monkeypatch.delenv("EVOLVE_SHOW_CULLED", raising=False)

    result = obs_mod.collect_observability(tmp_path)

    assert "display_cull" in result
    assert len(result["display_cull"]) == 1
    notice = result["display_cull"][0]
    assert "2 section" in notice
    assert "#379" in notice
    assert "EVOLVE_SHOW_CULLED" in notice


def test_no_notice_when_nothing_culled(monkeypatch, tmp_path: Path) -> None:
    fake_builders = [("kept", _builder(["## Kept", ""]))]
    monkeypatch.setattr(obs_mod, "_OBSERVABILITY_BUILDERS", fake_builders)
    monkeypatch.setattr(obs_mod.shrink_freeze, "CULLED_OBSERVABILITY_SECTIONS", frozenset())
    monkeypatch.delenv("EVOLVE_SHOW_CULLED", raising=False)

    result = obs_mod.collect_observability(tmp_path)

    assert "display_cull" not in result
