"""restore_state の icebox 棚卸し気づき通知（#194）。

毎朝の `gh issue list --label icebox --state closed` が `icebox-status.json` に保存した
凍結 issue の件数・最古経過日数を、SessionStart で systemMessage（ADR-038 = user 向け
チャネル）として surface する。

- icebox は evolve-anything 自身の GitHub issue backlog なので、**本体リポジトリ
  （`.claude-plugin/plugin.json` を持つ repo）で作業している時だけ**判定する（他 PJ では沈黙）。
- oldest_days が閾値未満 / ファイル無し → 沈黙（stdout を汚さない）。
- oldest_days が閾値以上 → systemMessage が出る。

env ガード: install レイアウト env のときだけ実環境 DATA_DIR を読む（evolve-queue notice と同型）。
書き込み先は tmp_path のみ。
"""
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import icebox_verdict_seen  # noqa: E402
import rl_common  # noqa: E402
import restore_state  # noqa: E402


def _call_icebox():
    """``_build_icebox_output`` は ExitStack を要求する（ADR-054 Phase 0 §5.3 ack 方式）。
    単体テストでは呼び出し単位で ExitStack を閉じ、lock を毎回解放する。
    """
    with ExitStack() as stack:
        return restore_state._build_icebox_output(stack)


# #351: _deliver_icebox_notice() は now を省略して呼ぶため実際の現在時刻と比較される
# （freshness gate）。固定の過去日付だと実行日が進むにつれ real now との差が
# stale_days を越え、これらの配線テスト（oldest_days の閾値判定を検証する意図）が
# 「generated_at 自体の stale 判定」を検証するテストに化けてしまう。生成時に毎回
# fresh な generated_at を埋め込む。generated_at 自体の freshness gate は
# scripts/lib/tests/test_icebox_notice.py が担当する。
def _fresh_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


# 以下 2つは変数名の "STALE"/"FRESH" は icebox 業務値（oldest_days が閾値超過か否か）を
# 指す（generated_at 自体の freshness とは無関係。generated_at は常に fresh に保つ）。
def _stale_status() -> dict:
    return {"count": 12, "oldest_days": 200, "generated_at": _fresh_generated_at()}


def _fresh_status() -> dict:
    return {"count": 3, "oldest_days": 10, "generated_at": _fresh_generated_at()}


def _write_status(data_dir: Path, payload: dict) -> None:
    (data_dir / "icebox-status.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_env(tmp_path, monkeypatch):
    """install レイアウト env をでっち上げ DATA_DIR を tmp に固定する。"""
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    # ADR-054 Phase 0: この test file の関心事でない他系統（data_dir_migration・
    # utterance_staleness）が同じ env ガードで一緒に発火し、handle_session_start 経由の
    # テストで2件以上＝digest 化してしまうのを防ぐ（queue_notice テストと同型）。
    monkeypatch.setattr(restore_state, "_build_data_dir_migration_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_utterance_staleness_output", lambda: None)
    return source


def _install_plugin_self_project(tmp_path, monkeypatch, is_self: bool = True):
    """CLAUDE_PROJECT_DIR を evolve-anything 本体 repo 相当（or 他 PJ）に設定する。"""
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    if is_self:
        (project_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (project_dir / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    return project_dir


def test_deliver_fires_with_stale_icebox_in_plugin_self_repo(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, _stale_status())
    item = _call_icebox()
    assert item is not None
    assert item.tier == 2
    assert "12件" in item.text
    assert "200日" in item.text
    assert item.digest == "icebox12件・最古200日"
    assert item.tail_link is True
    assert capsys.readouterr().out == ""  # 収集関数は印字しない


def test_deliver_silent_when_below_threshold(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, _fresh_status())
    assert _call_icebox() is None


def test_deliver_silent_when_no_icebox_file(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    _install_env(tmp_path, monkeypatch)
    assert _call_icebox() is None


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    assert _call_icebox() is None


def test_deliver_silent_without_data_env(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert _call_icebox() is None


def test_deliver_silent_outside_plugin_self_repo(tmp_path, monkeypatch, capsys):
    """evolve-anything 本体以外の PJ（plugin.json 無し）では、icebox が stale でも沈黙する。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=False)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, _stale_status())
    assert _call_icebox() is None


def test_deliver_silent_without_project_dir_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, _stale_status())
    assert _call_icebox() is None


def test_deliver_does_not_write(tmp_path, monkeypatch):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, _stale_status())
    before = {p.name for p in source.iterdir()}
    _call_icebox()
    after = {p.name for p in source.iterdir()}
    assert before == after  # icebox 通知は read-only


def test_handle_session_start_invokes_icebox_notice(tmp_path, monkeypatch, capsys):
    """handle_session_start が icebox 通知を配信フローに含む（配線回帰）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    _write_status(source, _stale_status())
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "12件" in out


def test_deliver_respects_custom_threshold_from_user_config(tmp_path, monkeypatch, capsys):
    """icebox_review_threshold_days を userConfig で下げると、デフォルト閾値未満の
    oldest_days でも発火する（#194 拡張）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    status = {"count": 2, "oldest_days": 25, "generated_at": _fresh_generated_at()}
    _write_status(source, status)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days", "20")
    item = _call_icebox()
    assert item is not None
    assert "25日" in item.text


def test_deliver_silent_below_custom_threshold(tmp_path, monkeypatch, capsys):
    """カスタム閾値未満なら（デフォルト閾値より小さい oldest_days でも）沈黙する。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    status = {"count": 2, "oldest_days": 45, "generated_at": _fresh_generated_at()}
    _write_status(source, status)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days", "60")
    assert _call_icebox() is None


def _verdict(number=1, lane="met", reason="weak_signals.unprocessed_count = 10 > 5 を満たしました"):
    return {"number": number, "lane": lane, "reason": reason, "value": 10}


def _write_verdicts(data_dir: Path, payload: dict) -> None:
    (data_dir / "icebox-verdicts.json").write_text(json.dumps(payload), encoding="utf-8")


def _call_icebox_and_commit():
    """収集 + ack の commit（print 成功後相当）まで一連で実行するテスト用ヘルパー。"""
    with ExitStack() as stack:
        item = restore_state._build_icebox_output(stack)
        if item is not None and item.commit is not None:
            item.commit()
        return item


# ─────────────────────────────────────────────────────────────────
# #352: icebox-verdicts.json レーン1「成立」通知 + 既読マーク（ADR-054 Phase 0 §5.3 ack 方式）
# ─────────────────────────────────────────────────────────────────
def test_deliver_names_met_issue_and_takes_priority_over_status(
    tmp_path, monkeypatch, capsys
):
    """成立 verdict があれば、icebox-status.json が stale でも成立通知が優先される。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_status(source, _stale_status())
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )
    item = _call_icebox_and_commit()
    assert item is not None
    assert item.tier == 1
    assert "#99" in item.text
    assert "weak_signals.unprocessed_count" in item.text
    assert "12件" not in item.text  # 件数集約通知には流れない
    assert capsys.readouterr().out == ""  # 収集関数は印字しない


def test_deliver_digest_is_short_frame_with_unchanged_body(tmp_path, monkeypatch):
    """§4.2 例外1: 混在時の短縮形は `icebox成立: {body}`。body（issue番号・reason）は不変。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )
    item = _call_icebox_and_commit()
    assert item is not None
    assert item.digest.startswith("icebox成立: ")
    assert "#99" in item.digest
    assert "weak_signals.unprocessed_count" in item.digest
    assert item.digest != item.text  # フル文とは別物（短縮フレーム）


def test_deliver_records_shown_verdict_as_seen(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )
    _call_icebox_and_commit()
    capsys.readouterr()  # 消費
    seen_keys = icebox_verdict_seen.read_seen_keys(source / "icebox_verdict_seen.jsonl")
    assert icebox_verdict_seen.verdict_key(_verdict(number=99)) in seen_keys


def test_deliver_does_not_record_seen_without_commit(tmp_path, monkeypatch):
    """ack 契約: commit を呼ばない限り既読化されない（print 失敗を模したケース）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )
    item = _call_icebox()  # commit しない
    assert item is not None
    assert item.commit is not None
    seen_keys = icebox_verdict_seen.read_seen_keys(source / "icebox_verdict_seen.jsonl")
    assert icebox_verdict_seen.verdict_key(_verdict(number=99)) not in seen_keys


def test_deliver_does_not_repeat_already_seen_met_verdict(tmp_path, monkeypatch, capsys):
    """1回目で成立通知→既読化。2回目（同じ評価値）は沈黙（icebox-status.json も無ければ）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )
    _call_icebox_and_commit()
    capsys.readouterr()
    assert _call_icebox_and_commit() is None


def test_deliver_does_not_reappear_when_only_reason_changes(tmp_path, monkeypatch, capsys):
    """B5 回帰テスト: lane/closed_at が同じなら reason（評価値由来）が変わっても再提示しない
    （fingerprint に value/reason を含めていた旧実装は毎日再通知される事故だった）。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99, reason="A")]},
    )
    _call_icebox_and_commit()
    capsys.readouterr()
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99, reason="B")]},
    )
    assert _call_icebox_and_commit() is None


def test_deliver_reappears_when_reclosed(tmp_path, monkeypatch, capsys):
    """再凍結（closed_at が変わる）は fingerprint が変わり再提示される。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    v1 = _verdict(number=99)
    v1["closed_at"] = "2026-01-01T00:00:00Z"
    _write_verdicts(source, {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [v1]})
    _call_icebox_and_commit()
    capsys.readouterr()
    v2 = _verdict(number=99)
    v2["closed_at"] = "2026-07-01T00:00:00Z"
    _write_verdicts(source, {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [v2]})
    item = _call_icebox_and_commit()
    assert item is not None  # 再凍結なので再提示される


def test_deliver_falls_back_to_status_when_no_met_verdicts(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_status(source, _stale_status())
    _write_verdicts(
        source,
        {
            "generated_at": "2026-08-01T09:00:00Z",
            "verdicts": [_verdict(number=1, lane="observer_missing")],
        },
    )
    item = _call_icebox()
    assert item is not None
    assert "12件" in item.text  # 従来の件数集約通知


def test_deliver_no_verdicts_file_falls_back_to_status(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_status(source, _stale_status())
    item = _call_icebox()
    assert item is not None
    assert "12件" in item.text


# ── P8: record_seen の write path が read path（seen_path）と明示的に一致する ──
def test_deliver_passes_explicit_seen_path_to_record_seen(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )
    calls = {}
    orig = icebox_verdict_seen.record_seen

    def spy(verdicts, *, path=None, dry_run=False):
        calls["path"] = path
        return orig(verdicts, path=path, dry_run=dry_run)

    monkeypatch.setattr(icebox_verdict_seen, "record_seen", spy)
    _call_icebox_and_commit()
    capsys.readouterr()
    assert calls["path"] == source / "icebox_verdict_seen.jsonl"


# ── P1: read-decide-print-write を file_lock で1トランザクション化 ──────
def test_deliver_serializes_via_file_lock(tmp_path, monkeypatch):
    """同時 SessionStart による二重通知を防ぐため file_lock で直列化する。
    外部でロックを保持している間は収集関数が完了しないことを検証する
    （learning_concurrency_test_by_lock_holding 方式・ロック保持中に進めないかを確認）。
    ADR-054 Phase 0: lock は decide〜commit まで保持されるため、``_call_icebox_and_commit``
    （収集 + commit を一連で行う）を使う。"""
    import fcntl
    import threading
    import time

    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )

    lock_path = source / "icebox_verdict_seen.jsonl.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "a", encoding="utf-8")
    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)  # 外部からロックを保持（他プロセス相当）

    finished = threading.Event()

    def _run():
        _call_icebox_and_commit()
        finished.set()

    t = threading.Thread(target=_run)
    t.start()
    try:
        time.sleep(0.2)
        assert not finished.is_set()  # ロック保持中は完了しない
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()
    t.join(timeout=5)
    assert finished.is_set()


def test_deliver_fires_with_default_threshold_no_override(tmp_path, monkeypatch, capsys):
    """env var 未設定でも新デフォルト30日が実際に使われ、30-89日の範囲
    （旧ライブラリデフォルト90日では沈黙するはずの範囲）で発火することを保証する。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    status = {"count": 4, "oldest_days": 45, "generated_at": _fresh_generated_at()}
    _write_status(source, status)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_icebox_review_threshold_days", raising=False)
    item = _call_icebox()
    assert item is not None
    assert "45日" in item.text


# ─────────────────────────────────────────────────────────────────
# icebox-status.json / icebox-verdicts.json 破損（§4.6/§5.4・新規判定）
# ─────────────────────────────────────────────────────────────────
def test_deliver_fires_health_notice_when_status_corrupt(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    (source / "icebox-status.json").write_text("{not valid json", encoding="utf-8")
    item = _call_icebox()
    assert item is not None
    assert item.tier == 1
    assert item.digest == "icebox破損"


def test_deliver_fires_health_notice_when_verdicts_corrupt(tmp_path, monkeypatch, capsys):
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    (source / "icebox-verdicts.json").write_text("{not valid json", encoding="utf-8")
    item = _call_icebox()
    assert item is not None
    assert item.tier == 1
    assert item.digest == "icebox破損"


# ─────────────────────────────────────────────────────────────────
# レーン1「成立」の失敗3経路 E2E（§5.3/§7.1-5・ack 方式が commit を defer することの確認）
# ─────────────────────────────────────────────────────────────────
def _silence_other_notifications(monkeypatch):
    for name in (
        "_build_pending_trigger_output",
        "_build_spec_drift_output",
        "_build_evolve_drain_output",
        "_build_data_dir_migration_output",
        "_build_utterance_staleness_output",
        "_build_evolve_queue_output",
        "_build_session_proposal_output",
        "_build_judge_cap_output",
    ):
        monkeypatch.setattr(restore_state, name, lambda *a, **k: None)


def test_lane1_failure_path_builder_exception_keeps_unseen(tmp_path, monkeypatch, capsys):
    """失敗経路①: レーン1収集の内部例外 → record_seen 未実行・次回も未既読のまま。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _silence_other_notifications(monkeypatch)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )

    orig_read_seen_keys = icebox_verdict_seen.read_seen_keys

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(icebox_verdict_seen, "read_seen_keys", _boom)

    restore_state.handle_session_start({})

    # アサーション自体が patch の影響を受けないよう、退避した本物の関数で確認する。
    seen_keys = orig_read_seen_keys(source / "icebox_verdict_seen.jsonl")
    assert icebox_verdict_seen.verdict_key(_verdict(number=99)) not in seen_keys


def test_lane1_failure_path_merge_exception_keeps_unseen(tmp_path, monkeypatch, capsys):
    """失敗経路②: merge 関数の例外 → commit されず未既読のまま。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _silence_other_notifications(monkeypatch)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )

    def _boom(items):
        raise RuntimeError("boom in merge")

    monkeypatch.setattr(restore_state, "_merge_notification_text", _boom)

    restore_state.handle_session_start({})

    seen_keys = icebox_verdict_seen.read_seen_keys(source / "icebox_verdict_seen.jsonl")
    assert icebox_verdict_seen.verdict_key(_verdict(number=99)) not in seen_keys


def test_lane1_failure_path_print_exception_keeps_unseen(tmp_path, monkeypatch, capsys):
    """失敗経路③: print/json.dumps 自体の失敗 → commit されず未既読のまま。"""
    _install_plugin_self_project(tmp_path, monkeypatch, is_self=True)
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.setattr(rl_common, "DATA_DIR", source)
    _silence_other_notifications(monkeypatch)
    _write_verdicts(
        source,
        {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict(number=99)]},
    )

    def _boom(*a, **k):
        raise TypeError("not serializable")

    monkeypatch.setattr(restore_state.json, "dumps", _boom)

    restore_state.handle_session_start({})
    monkeypatch.undo()  # json.dumps を戻す（icebox_verdict_seen 側も同一 json モジュールを使うため）

    seen_keys = icebox_verdict_seen.read_seen_keys(source / "icebox_verdict_seen.jsonl")
    assert icebox_verdict_seen.verdict_key(_verdict(number=99)) not in seen_keys
