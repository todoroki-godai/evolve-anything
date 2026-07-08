"""daily（毎朝の定期 evolve queue 実行 + SessionStart 通知）の単体テスト（#80 Phase 1b）。

- plist 生成: label / 実行時刻 (StartCalendarInterval) / ProgramArguments / ログパスが正しく埋まるか。
- daily runner コマンド: `fleet ingest` → `fleet queue --json` → evolve-queue.json 保存のシェル文字列。
- queue 通知: queue 有 / 無（空 queue=無音）/ stale（generated_at が N 日前→advisory）。

すべて決定論・LLM 非依存（ingest/queue は別プロセス・シェル文字列としてのみ参照）。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily import plist as plist_mod
from daily import queue_notice as qn


# ---- 共有 fixture（issue の exact スキーマ・待ち2件）----
SAMPLE_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
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

EMPTY_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
}


# ===== plist 生成 =====
def test_build_plist_embeds_label_time_program_and_log():
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d/evolve-anything",
        hour=9,
        minute=30,
    )
    assert plist_mod.LAUNCHD_LABEL in out
    # 実行時刻
    assert "<key>Hour</key>" in out
    assert "<integer>9</integer>" in out
    assert "<key>Minute</key>" in out
    assert "<integer>30</integer>" in out
    # runner スクリプトが ProgramArguments に入る
    assert "/p/evolve-anything/bin/evolve-daily-run" in out
    # ログパス（stdout/stderr）
    assert "/d/evolve-anything/logs/evolve-daily.log" in out
    # 妥当な plist 構造
    assert out.startswith("<?xml")
    assert "<key>StartCalendarInterval</key>" in out


def test_build_plist_default_time_is_0900():
    out = plist_mod.build_plist(plugin_root="/p", data_dir="/d")
    assert "<integer>9</integer>" in out  # Hour
    assert "<integer>0</integer>" in out  # Minute


def test_build_plist_pins_python_exe_when_given():
    """python_exe 指定時、ProgramArguments が [python_exe, runner] の順（shebang 迂回で 3.9 死回避）。"""
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d",
        python_exe="/opt/homebrew/bin/python3.14",
    )
    runner = "/p/evolve-anything/bin/evolve-daily-run"
    py = "/opt/homebrew/bin/python3.14"
    assert f"<string>{py}</string>" in out
    assert f"<string>{runner}</string>" in out
    # python_exe が runner より前（launchd は python_exe に runner を渡して起動する）
    assert out.index(f"<string>{py}</string>") < out.index(f"<string>{runner}</string>")


def test_build_plist_omits_python_exe_by_default():
    """python_exe 省略時は ProgramArguments が runner のみ（後方互換）。"""
    out = plist_mod.build_plist(plugin_root="/p/evolve-anything", data_dir="/d")
    runner = "/p/evolve-anything/bin/evolve-daily-run"
    # ProgramArguments の array に runner だけ入る
    array = out.split("<key>ProgramArguments</key>", 1)[1].split("</array>", 1)[0]
    assert f"<string>{runner}</string>" in array
    assert array.count("<string>") == 1


def test_plist_path_under_launchagents():
    p = plist_mod.plist_path()
    assert p.name == f"{plist_mod.LAUNCHD_LABEL}.plist"
    assert p.parent.name == "LaunchAgents"


def test_daily_command_runs_ingest_then_queue_and_writes_json():
    """runner コマンド文字列が ingest → queue --json → evolve-queue.json 保存の順を含む。"""
    cmd = plist_mod.daily_command_str(
        fleet_bin="/p/bin/evolve-fleet",
        out_path="/d/evolve-anything/evolve-queue.json",
    )
    assert "/p/bin/evolve-fleet ingest" in cmd
    assert "/p/bin/evolve-fleet queue --json" in cmd
    assert "/d/evolve-anything/evolve-queue.json" in cmd
    # ingest が queue より前
    assert cmd.index("ingest") < cmd.index("queue --json")


# ===== queue 通知（reader + メッセージ生成）=====
def test_read_queue_returns_parsed_dict(tmp_path):
    qfile = tmp_path / "evolve-queue.json"
    qfile.write_text(json.dumps(SAMPLE_QUEUE), encoding="utf-8")
    data = qn.read_queue(tmp_path)
    assert data is not None
    assert data["queue"][0]["pj_slug"] == "figma-to-code"


def test_read_queue_missing_file_returns_none(tmp_path):
    assert qn.read_queue(tmp_path) is None


def test_read_queue_corrupt_file_returns_none(tmp_path):
    (tmp_path / "evolve-queue.json").write_text("{not json", encoding="utf-8")
    assert qn.read_queue(tmp_path) is None


def test_notice_lists_waiting_pjs():
    # generated_at と同日に評価 → not stale
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(SAMPLE_QUEUE, now=now)
    assert msg is not None
    assert "figma-to-code" in msg
    assert "sys-bots" in msg
    assert "2" in msg  # N件
    assert "evolve" in msg


def test_notice_empty_queue_is_silent():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_queue_notice(EMPTY_QUEUE, now=now) is None


def test_notice_none_input_is_silent():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_queue_notice(None, now=now) is None


def test_notice_stale_queue_gets_advisory():
    # generated_at から 5 日後 → stale advisory が付く
    now = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(SAMPLE_QUEUE, now=now, stale_days=2)
    assert msg is not None
    assert "figma-to-code" in msg
    # stale 文言（日数）
    assert "5" in msg


def test_notice_fresh_queue_has_no_stale_advisory():
    now = datetime(2026, 6, 25, 9, 0, 30, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(SAMPLE_QUEUE, now=now, stale_days=2)
    assert msg is not None
    # 直近生成なので「日前」は出ない
    assert "日前" not in msg


def test_notice_malformed_generated_at_does_not_crash():
    bad = dict(SAMPLE_QUEUE)
    bad = json.loads(json.dumps(SAMPLE_QUEUE))
    bad["generated_at"] = "not-a-timestamp"
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    # クラッシュせず通知は出る（stale 判定不能なら advisory なし）
    msg = qn.build_queue_notice(bad, now=now, stale_days=2)
    assert msg is not None
    assert "figma-to-code" in msg


def test_systemmessage_output_dict():
    """CC hook 出力用に systemMessage dict を返すヘルパ。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    out = qn.queue_notice_output(SAMPLE_QUEUE, now=now)
    assert out is not None
    assert "systemMessage" in out
    assert "figma-to-code" in out["systemMessage"]


def test_systemmessage_output_silent_when_empty():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.queue_notice_output(EMPTY_QUEUE, now=now) is None
