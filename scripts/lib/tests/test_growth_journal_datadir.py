#!/usr/bin/env python3
"""growth_journal の DATA_DIR call-time 解決テスト（#420 残課題 / テスト汚染根治）。

旧実装は import 時に ``_DATA_DIR_VAL = _common.DATA_DIR`` でコピー固定していたため、
モジュール単体では env / ``mock.patch.object(rl_common, "DATA_DIR", ...)`` に追従できず、
conftest の ``_rebase_module_data_dirs``（#420）の機械 rebase に依存して実
~/.claude/evolve-anything/ の汚染を免れていた（2026-06-10 に test_* 56 件が実 store に
書かれた残渣が動かぬ証拠）。本テストは ``_data_dir()`` が call-time に
``rl_common.DATA_DIR`` を参照する正パターン（store_write と同型）であることを assert する。
"""
from pathlib import Path

import pytest


def test_data_dir_follows_calltime_rl_common(monkeypatch, tmp_path):
    """_data_dir() は call-time に rl_common.DATA_DIR を参照する（import 時コピーでない）。"""
    import rl_common
    import growth_journal

    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)
    assert growth_journal._data_dir() == tmp_path


def test_data_dir_tracks_repatched_value(monkeypatch, tmp_path):
    """patch 値を変えるたびに _data_dir() が追従する（固定コピーなら不変で fail）。"""
    import rl_common
    import growth_journal

    first = tmp_path / "a"
    second = tmp_path / "b"
    monkeypatch.setattr(rl_common, "DATA_DIR", first)
    assert growth_journal._data_dir() == first
    monkeypatch.setattr(rl_common, "DATA_DIR", second)
    assert growth_journal._data_dir() == second


def test_emit_writes_to_patched_datadir(monkeypatch, tmp_path):
    """patch 後の emit が tmp に書かれ実 home を汚染しない（汚染根治の本丸）。"""
    import rl_common
    import growth_journal

    real_home_evolve = (Path.home() / ".claude" / "evolve-anything").resolve()
    target = tmp_path / "isolated"
    monkeypatch.setattr(rl_common, "DATA_DIR", target)

    growth_journal.emit_crystallization(
        project="test_isolation_proof",
        targets=["rules/x.md"],
        evidence_count=1,
        phase="proof",
    )

    written = target / "growth-journal.jsonl"
    assert written.exists(), "emit が patch 後の DATA_DIR に書いていない"
    assert "test_isolation_proof" in written.read_text(encoding="utf-8")
    # call-time 参照ゆえ解決先は patch 値であり実 home 配下ではない。
    assert real_home_evolve not in written.resolve().parents


def test_data_dir_no_module_level_copy():
    """_DATA_DIR_VAL の import 時コピー属性を残さない（conftest rebase 依存の撤去）。"""
    import growth_journal

    assert not hasattr(growth_journal, "_DATA_DIR_VAL"), (
        "_DATA_DIR_VAL の import 時コピーが残存している。call-time 参照に統一すること。"
    )
