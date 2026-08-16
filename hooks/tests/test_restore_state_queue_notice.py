"""restore_state の evolve-queue 通知（#80 Phase 1b）。

毎朝の `fleet ingest`→`fleet queue` が `evolve-queue.json` に保存した待ち PJ を、
SessionStart で systemMessage（ADR-038 = user 向けチャネル）として surface する。

- queue 有 → systemMessage に待ち PJ 一覧が出る
- 空 queue / ファイル無し → 沈黙（stdout を汚さない）
- stale（generated_at が古い）→ advisory が付く

env ガード: install レイアウト env のときだけ実環境 DATA_DIR を読む（utterance staleness と同型）。
書き込み先は tmp_path のみ。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402


# #351: _build_evolve_queue_output() は now を省略して呼ぶため実際の現在時刻と
# 比較される（freshness gate）。固定の過去日付だと実行日が進むにつれ real now との
# 差が stale_days を越え、これらの配線テストが「業務値を検証する」意図から外れて
# 「stale 判定を検証する」テストに化けてしまう。生成時に毎回 fresh な generated_at を
# 埋め込み、配線（figma-to-code が出るか等）を安定して検証する。stale 自体の判定は
# scripts/lib/tests/test_daily.py::test_notice_stale_queue_replaces_business_content_with_health_notice
# が担当する。
def _fresh_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sample_queue() -> dict:
    return {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 10,
        "queue": [
            {
                "pj_slug": "figma-to-code",
                "material_count": 9,
                "weak_unprocessed": 7,
                "new_corrections": 2,
                "last_evolve_at": "2026-06-20T10:00:00Z",
                "activity_since": {"subagents": 40, "sessions": 5},
                "reason": "weak=7 + new corr=2 >= 3",
            },
            {
                "pj_slug": "sys-bots",
                "material_count": 4,
                "weak_unprocessed": 4,
                "new_corrections": 0,
                "last_evolve_at": None,
                "activity_since": {"subagents": 12, "sessions": 3},
                "reason": "weak=4 (初回)",
            },
        ],
    }


def _empty_queue() -> dict:
    return {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 10,
        "queue": [],
    }


def _write_queue(data_dir: Path, payload: dict) -> None:
    (data_dir / "evolve-queue.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_env(tmp_path, monkeypatch):
    """install レイアウト env をでっち上げ DATA_DIR を tmp に固定する。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    # ADR-054 Phase 0: is_cc_install_layout を source にマッチさせると、この test file の
    # 関心事でない他系統（data_dir_migration・utterance_staleness）も同じ env ガードで
    # 一緒に発火し、handle_session_start 経由のテストで2件以上＝digest 化してしまう
    # （フル文の PJ 名等が消える）。ここで明示的に無効化する。
    monkeypatch.setattr(restore_state, "_build_data_dir_migration_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_utterance_staleness_output", lambda: None)
    # rl_common.resolve_data_dir は env をそのまま返す（marker 無し）。
    return source


def test_deliver_fires_with_waiting_queue(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _sample_queue())
    item = restore_state._build_evolve_queue_output()
    assert item is not None
    assert item.tier == 2
    assert "figma-to-code" in item.text
    assert "sys-bots" in item.text
    assert item.digest == "evolve待ち2PJ"
    assert item.tail_link is True
    assert capsys.readouterr().out == ""  # 収集関数は印字しない


def test_deliver_silent_on_empty_queue(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _empty_queue())
    assert restore_state._build_evolve_queue_output() is None


def test_deliver_silent_when_no_queue_file(tmp_path, monkeypatch, capsys):
    _install_env(tmp_path, monkeypatch)
    assert restore_state._build_evolve_queue_output() is None


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    assert restore_state._build_evolve_queue_output() is None


def test_deliver_silent_without_env(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert restore_state._build_evolve_queue_output() is None


def test_deliver_does_not_write(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _sample_queue())
    before = {p.name for p in source.iterdir()}
    restore_state._build_evolve_queue_output()
    after = {p.name for p in source.iterdir()}
    assert before == after  # queue 通知は read-only


def test_deliver_stale_at_30_hours_shows_dedicated_message(tmp_path, monkeypatch, capsys):
    """#466: 既定 stale_hours=30 が末端の restore_state 配線まで貫通していること。
    queue 専用メッセージに切り替わり、freshness.health_notice の汎用文は出ない。"""
    source = _install_env(tmp_path, monkeypatch)
    stale_generated_at = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    _write_queue(source, dict(_sample_queue(), generated_at=stale_generated_at))
    item = restore_state._build_evolve_queue_output()
    assert item is not None
    assert item.tier == 1
    assert "figma-to-code" not in item.text
    assert "学習データの自動取り込みが止まっています" in item.text
    assert "30時間前" in item.text
    assert "現在値は不明です" not in item.text
    assert item.digest == "毎朝の取り込みが30時間停止"


def test_deliver_unknown_generated_at_shows_dedicated_message(tmp_path, monkeypatch, capsys):
    """#466: generated_at が壊れている（UNKNOWN）場合も queue 専用メッセージ。"""
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, dict(_sample_queue(), generated_at="not-a-timestamp"))
    item = restore_state._build_evolve_queue_output()
    assert item is not None
    assert item.tier == 1
    assert "figma-to-code" not in item.text
    assert "動いているか判定できません" in item.text
    assert "現在値は不明です" not in item.text
    assert item.digest == "毎朝の取り込みが判定不能"


def test_handle_session_start_invokes_queue_notice(tmp_path, monkeypatch, capsys):
    """handle_session_start が queue 通知を配信フローに含む（配線回帰）。"""
    source = _install_env(tmp_path, monkeypatch)
    _write_queue(source, _sample_queue())
    # checkpoint 無し環境（CLAUDE_PROJECT_DIR を tmp に向ける）
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "figma-to-code" in out
