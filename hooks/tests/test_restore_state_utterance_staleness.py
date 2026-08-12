"""restore_state の utterance アーカイブ staleness advisory（#430）。

observe-first pre-flight: marker ファイルを読むだけで DuckDB 接続も transcript
走査もしない。marker 不在 = 「未 ingest」= advisory（∞ 扱い・0日でない）。閾値14日。

純関数 `utterance_staleness_advisory(data_dir)` を直接検証し、`_deliver_*` は
install レイアウト env のときだけ発火する（テスト isolation の tmp env では沈黙して
JSON stdout を汚さない）ことを固定する。書き込み先は tmp_path のみ。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402
from utterance_archive import store as ustore  # noqa: E402


# --- 純関数 utterance_staleness_advisory -------------------------------------

def test_advisory_when_marker_absent(tmp_path):
    """marker 不在 = 未 ingest → advisory を返す。"""
    msg = restore_state.utterance_staleness_advisory(tmp_path)
    assert msg is not None
    assert "evolve-fleet ingest" in msg
    assert "未 ingest" in msg


def test_advisory_when_stale(tmp_path):
    """最終 ingest が 14 日より古ければ advisory を返す。"""
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    (tmp_path / ustore.MARKER_NAME).write_text(old, encoding="utf-8")
    msg = restore_state.utterance_staleness_advisory(tmp_path)
    assert msg is not None
    assert "evolve-fleet ingest" in msg


def test_advisory_none_when_fresh(tmp_path):
    """最近 ingest 済みなら None（沈黙）。"""
    ustore.write_last_ingest_at(tmp_path)
    assert restore_state.utterance_staleness_advisory(tmp_path) is None


def test_advisory_does_not_write(tmp_path):
    """advisory は marker を読むだけ — DB も marker も書かない（副作用ゼロ）。"""
    restore_state.utterance_staleness_advisory(tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- _build_utterance_staleness_output の env ガード ---------------------------

def test_deliver_fires_in_install_layout(tmp_path, monkeypatch, capsys):
    """install レイアウト env では advisory を収集する（Tier1・印字しない）。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    item = restore_state._build_utterance_staleness_output()
    assert item is not None
    assert item.tier == 1
    assert "evolve-fleet ingest" in item.text
    assert item.digest == "発話取込停止（要ingest）"  # marker 不在（未 ingest）は日数無し
    assert capsys.readouterr().out == ""  # 収集関数は印字しない


def test_deliver_digest_includes_age_days_when_stale(tmp_path, monkeypatch, capsys):
    """marker があり stale な場合、digest に経過日数が入る。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    (source / ustore.MARKER_NAME).write_text(old, encoding="utf-8")
    item = restore_state._build_utterance_staleness_output()
    assert item is not None
    assert item.digest == "発話取込20日停止（要ingest）"


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    """テスト isolation の tmp env では発火せず JSON stdout を汚さない。"""
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    assert restore_state._build_utterance_staleness_output() is None
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_env(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert restore_state._build_utterance_staleness_output() is None
    assert capsys.readouterr().out == ""
