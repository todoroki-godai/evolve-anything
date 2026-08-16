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


def test_build_plist_sets_path_env_to_pin_child_python():
    """python_exe 指定時、EnvironmentVariables の PATH に python の dir を先頭付与する。

    runner が bare パスで spawn する子（evolve-fleet の #!/usr/bin/env python3）が launchd の
    最小 PATH（/opt/homebrew 無し）で /usr/bin の 3.9 に落ちるのを防ぐ。
    """
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d",
        python_exe="/opt/homebrew/opt/python@3.14/bin/python3.14",
    )
    assert "<key>PATH</key>" in out
    # python の dir が先頭、system パスが後続
    assert "/opt/homebrew/opt/python@3.14/bin:/usr/bin:/bin:/usr/sbin:/sbin" in out


def test_build_plist_appends_extra_path_dirs_for_child_tools():
    """extra_path_dirs（gh 等の子ツール dir）が python dir の後・system パスの前に入る（#196）。

    launchd の最小 PATH には /opt/homebrew/bin が無く、icebox 集計（#194）の gh が
    FileNotFoundError で恒久 fail-open になる。install 時に検出した gh の dir を焼き込む。
    """
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d",
        python_exe="/opt/homebrew/opt/python@3.14/bin/python3.14",
        extra_path_dirs=("/opt/homebrew/bin",),
    )
    assert (
        "/opt/homebrew/opt/python@3.14/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        in out
    )


def test_build_plist_dedupes_extra_path_dirs():
    """extra dir が python dir と同一なら重複させない。"""
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d",
        python_exe="/opt/homebrew/bin/python3.14",
        extra_path_dirs=("/opt/homebrew/bin",),
    )
    assert "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" in out
    assert "/opt/homebrew/bin:/opt/homebrew/bin" not in out


def test_build_plist_env_includes_claude_plugin_data():
    """CLAUDE_PLUGIN_DATA が EnvironmentVariables に残る（env_entries ループ化後の回帰ガード）。"""
    out = plist_mod.build_plist(plugin_root="/p/evolve-anything", data_dir="/d & e")
    assert "<key>CLAUDE_PLUGIN_DATA</key>" in out
    assert "<string>/d &amp; e</string>" in out


def test_build_plist_bare_python_exe_does_not_inject_empty_path_segment():
    """bare 名の python_exe（dirname が空）で PATH に空セグメントを作らない。

    POSIX の PATH 空エントリはカレントディレクトリを意味し、launchd ジョブの CWD に置かれた
    同名バイナリが優先実行される PATH インジェクションになるため、plist に焼き込まない。
    現行の install() は常に絶対パス（sys.executable）を渡すが、build_plist は public 関数
    なので将来の呼び出し元に対するガード。
    """
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything", data_dir="/d", python_exe="python3.14"
    )
    assert "<key>PATH</key>" not in out

    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d",
        python_exe="python3.14",
        extra_path_dirs=("/opt/homebrew/bin",),
    )
    assert "<string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>" in out


def test_build_plist_extra_path_dirs_without_python_exe():
    """extra_path_dirs は python_exe 無しでも silent drop されず PATH に入る。"""
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything", data_dir="/d", extra_path_dirs=("/opt/homebrew/bin",)
    )
    assert "<string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>" in out


def test_build_plist_dedupes_system_dirs_in_extra():
    """extra_path_dirs が末尾 system パスと同一なら重複させない。"""
    out = plist_mod.build_plist(
        plugin_root="/p/evolve-anything",
        data_dir="/d",
        python_exe="/opt/homebrew/bin/python3.14",
        extra_path_dirs=("/usr/bin",),
    )
    assert "<string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>" in out


def test_build_plist_omits_python_exe_by_default():
    """python_exe 省略時は ProgramArguments が runner のみ・PATH 無し（後方互換）。"""
    out = plist_mod.build_plist(plugin_root="/p/evolve-anything", data_dir="/d")
    runner = "/p/evolve-anything/bin/evolve-daily-run"
    # ProgramArguments の array に runner だけ入る
    array = out.split("<key>ProgramArguments</key>", 1)[1].split("</array>", 1)[0]
    assert f"<string>{runner}</string>" in array
    assert array.count("<string>") == 1
    # python_exe も extra_path_dirs も無しなら PATH 注入もしない
    assert "<key>PATH</key>" not in out


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


def test_notice_stale_queue_replaces_business_content_with_health_notice():
    """#351: stale なら旧値（PJ 一覧）を併記せず、専用メッセージに差し替える（旧実装は
    業務値の後ろに advisory を追記するだけだった）。#466: 経過時間は時間単位で表示し、
    freshness.health_notice の汎用文（「現在値は不明です」）は使わない。"""
    now = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone.utc)  # generated_at から 5 日後
    msg = qn.build_queue_notice(SAMPLE_QUEUE, now=now, stale_days=2)
    assert msg is not None
    assert "figma-to-code" not in msg
    assert "sys-bots" not in msg
    assert "120時間前" in msg
    assert "現在値は不明です" not in msg
    assert "学習データの自動取り込みが止まっています" in msg


def test_notice_fresh_queue_has_no_stale_advisory():
    now = datetime(2026, 6, 25, 9, 0, 30, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(SAMPLE_QUEUE, now=now, stale_days=2)
    assert msg is not None
    # 直近生成なので「日前」は出ない
    assert "日前" not in msg


def test_notice_malformed_generated_at_is_unknown_and_does_not_leak_business_values():
    """#351: generated_at がパース不能なら freshness gate が先に働き、業務値（PJ 名等）を
    一切解釈しない専用メッセージを返す（判定不能でも通知は出すが、旧仕様のように
    queue の中身をそのまま見せない）。#466: freshness.health_notice の汎用文は使わない。"""
    bad = json.loads(json.dumps(SAMPLE_QUEUE))
    bad["generated_at"] = "not-a-timestamp"
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(bad, now=now, stale_days=2)
    assert msg is not None
    assert "figma-to-code" not in msg
    assert "現在値は不明です" not in msg
    assert "動いているか判定できません" in msg


def test_notice_missing_generated_at_is_unknown():
    bad = json.loads(json.dumps(SAMPLE_QUEUE))
    del bad["generated_at"]
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(bad, now=now, stale_days=2)
    assert msg is not None
    assert "現在値は不明です" not in msg
    assert "動いているか判定できません" in msg


def test_notice_future_generated_at_is_unknown():
    bad = json.loads(json.dumps(SAMPLE_QUEUE))
    bad["generated_at"] = "2099-01-01T00:00:00Z"
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(bad, now=now, stale_days=2)
    assert msg is not None
    assert "現在値は不明です" not in msg
    assert "動いているか判定できません" in msg


def test_notice_naive_generated_at_without_tz_is_unknown():
    bad = json.loads(json.dumps(SAMPLE_QUEUE))
    bad["generated_at"] = "2026-06-25T09:00:00"
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_queue_notice(bad, now=now, stale_days=2)
    assert msg is not None
    assert "現在値は不明です" not in msg
    assert "動いているか判定できません" in msg


def test_notice_empty_queue_with_stale_generated_at_still_warns():
    """#351 回帰テスト: producer が最後に空 queue を書いた直後に停止すると、旧実装は
    「業務値=空だから沈黙」を優先し stale を恒久的に見逃していた。空でも generated_at が
    stale なら stale 通知が出ること。"""
    stale_empty = json.loads(json.dumps(EMPTY_QUEUE))
    now = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)  # generated_at から 6 日後
    msg = qn.build_queue_notice(stale_empty, now=now, stale_days=2)
    assert msg is not None
    assert "学習データの自動取り込みが止まっています" in msg


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


# ===== #466: 既定は DEFAULT_STALE_HOURS（30時間）— build_queue_notice の stale_days 引数
# に関わらず内部で stale_hours を強制する。停止に「その日のうち」に気づけるようにする。
GENERATED_AT_0900 = "2026-06-25T09:00:00Z"


def test_default_build_queue_notice_fresh_at_23_hours():
    """09:00 実行 → 翌朝 08:00（23時間後）は正常な沈黙域として FRESH のまま。"""
    now = datetime(2026, 6, 26, 8, 0, 0, tzinfo=timezone.utc)
    queue = dict(SAMPLE_QUEUE, generated_at=GENERATED_AT_0900)
    msg = qn.build_queue_notice(queue, now=now)
    assert msg is not None
    assert "figma-to-code" in msg  # health notice でなく通常の待ち一覧


def test_default_build_queue_notice_fresh_at_29_hours():
    now = datetime(2026, 6, 26, 14, 0, 0, tzinfo=timezone.utc)  # +29h
    queue = dict(SAMPLE_QUEUE, generated_at=GENERATED_AT_0900)
    msg = qn.build_queue_notice(queue, now=now)
    assert msg is not None
    assert "figma-to-code" in msg


def test_default_build_queue_notice_stale_at_30_hours():
    """30時間ちょうどで STALE（当日中に気づける粒度・#466）。専用メッセージに切り替わり
    health_notice の汎用文（「現在値は不明です」）は出ない。"""
    now = datetime(2026, 6, 26, 15, 0, 0, tzinfo=timezone.utc)  # +30h
    queue = dict(SAMPLE_QUEUE, generated_at=GENERATED_AT_0900)
    msg = qn.build_queue_notice(queue, now=now)
    assert msg is not None
    assert "figma-to-code" not in msg
    assert "学習データの自動取り込みが止まっています" in msg
    assert "30時間前" in msg
    assert "現在値は不明です" not in msg


def test_default_build_queue_notice_stale_message_content():
    """#466 是正: 「N時間で即・不合格確定」でなく「週の締切を過ぎると欠測確定」を伝える。"""
    now = datetime(2026, 6, 26, 15, 0, 0, tzinfo=timezone.utc)  # +30h
    queue = dict(SAMPLE_QUEUE, generated_at=GENERATED_AT_0900)
    msg = qn.build_queue_notice(queue, now=now)
    assert msg is not None
    assert "bin/evolve-daily-run" in msg
    assert "週の締切" in msg
    assert "4週連続" in msg
    assert "launchctl list | grep com.evolve-anything.daily" in msg


def test_default_build_queue_notice_unknown_uses_dedicated_message_not_generic_health_notice():
    """UNKNOWN（generated_at 欠落等）でも health_notice の汎用文を使わない。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    bad = dict(SAMPLE_QUEUE, generated_at="not-a-timestamp")
    msg = qn.build_queue_notice(bad, now=now)
    assert msg is not None
    assert "figma-to-code" not in msg
    assert "現在値は不明です" not in msg
    assert "動いているか判定できません" in msg
    assert "bin/evolve-daily-run" in msg


def test_default_build_judge_cap_notice_silent_at_30_hours_stale():
    """build_queue_notice 同様、既定 30 時間閾値を judge cap notice も共有する（二重通知防止）。"""
    now = datetime(2026, 6, 26, 15, 0, 0, tzinfo=timezone.utc)  # +30h
    stale = dict(CAPPED_QUEUE, generated_at=GENERATED_AT_0900)
    assert qn.build_judge_cap_notice(stale, now=now) is None


def test_default_build_judge_cap_notice_fires_at_29_hours_fresh():
    now = datetime(2026, 6, 26, 14, 0, 0, tzinfo=timezone.utc)  # +29h
    fresh = dict(CAPPED_QUEUE, generated_at=GENERATED_AT_0900)
    msg = qn.build_judge_cap_notice(fresh, now=now)
    assert msg is not None


# ===== llm_judge 日次上限到達通知（#408・evolve-queue.json の llm_judge フィールドを再利用）=====
CAPPED_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
    "llm_judge": {
        "unjudged_before": 250,
        "selected": 200,
        "capped": True,
        "corrections": 5,
        "call_failed": 0,
    },
}

NOT_CAPPED_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
    "llm_judge": {
        "unjudged_before": 3,
        "selected": 3,
        "capped": False,
        "corrections": 1,
        "call_failed": 0,
    },
}


def test_judge_cap_notice_fires_when_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_judge_cap_notice(CAPPED_QUEUE, now=now)
    assert msg is not None
    assert "200" in msg
    assert "50" in msg  # 残り件数 = 250 - 200


def test_judge_cap_notice_silent_when_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_judge_cap_notice(NOT_CAPPED_QUEUE, now=now) is None


def test_judge_cap_notice_silent_when_llm_judge_key_missing():
    """llm_judge フィールドが無い（旧世代の evolve-queue.json）→ 沈黙。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_judge_cap_notice(SAMPLE_QUEUE, now=now) is None


def test_judge_cap_notice_silent_when_none_input():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_judge_cap_notice(None, now=now) is None


def test_judge_cap_notice_silent_when_stale():
    """freshness gate は evolve-queue.json 全体の generated_at を共有する（二重通知しない
    — health notice は build_queue_notice 側が既に出すため、こちらは沈黙する）。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    stale = dict(CAPPED_QUEUE, generated_at="2026-06-01T00:00:00Z")
    assert qn.build_judge_cap_notice(stale, now=now, stale_days=2) is None


def test_judge_cap_notice_output_dict():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    out = qn.judge_cap_notice_output(CAPPED_QUEUE, now=now)
    assert out is not None
    assert "systemMessage" in out


def test_judge_cap_notice_output_silent_when_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.judge_cap_notice_output(NOT_CAPPED_QUEUE, now=now) is None


# ── #410 [Must]E: 発話ソース DB/schema 障害は capped=False でも沈黙させない ──────
SOURCE_FAILED_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
    "llm_judge": {
        "unjudged_before": 0,
        "selected": 0,
        "capped": False,
        "corrections": 0,
        "call_failed": 0,
        "source_failed": True,
        "source_error": "RuntimeError: duckdb schema mismatch",
    },
}


def test_judge_cap_notice_fires_when_source_failed_even_if_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_judge_cap_notice(SOURCE_FAILED_QUEUE, now=now)
    assert msg is not None
    assert "duckdb schema mismatch" in msg


def test_judge_cap_notice_silent_when_source_failed_false_and_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_judge_cap_notice(NOT_CAPPED_QUEUE, now=now) is None


def test_judge_cap_notice_source_failed_takes_priority_over_capped_message():
    """source_failed=True かつ capped=True でも障害の方を優先して伝える（原因の方が重要）。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    both = dict(CAPPED_QUEUE)
    both["llm_judge"] = dict(CAPPED_QUEUE["llm_judge"], source_failed=True, source_error="boom")
    msg = qn.build_judge_cap_notice(both, now=now)
    assert "boom" in msg


# ── #410 round3 [Should]4: skipped_locked（別プロセスが lock 保持中で non-blocking skip
# した）は source_failed / capped と並んで surface すべきなのに、build_judge_cap_notice は
# この2つしか見ていなかった。skip が連日続く（供給停止）のに沈黙するのを防ぐ。
SKIPPED_LOCKED_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
    "llm_judge": {
        "unjudged_before": 0,
        "selected": 0,
        "capped": False,
        "corrections": 0,
        "call_failed": 0,
        "source_failed": False,
        "source_error": None,
        "skipped_locked": True,
    },
}


def test_judge_cap_notice_fires_when_skipped_locked_even_if_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_judge_cap_notice(SKIPPED_LOCKED_QUEUE, now=now)
    assert msg is not None
    assert "別プロセス" in msg


def test_judge_cap_notice_silent_when_skipped_locked_false_and_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_judge_cap_notice(NOT_CAPPED_QUEUE, now=now) is None


def test_judge_cap_notice_source_failed_takes_priority_over_skipped_locked_message():
    """source_failed=True かつ skipped_locked=True でも障害の方を優先して伝える。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    both = dict(SKIPPED_LOCKED_QUEUE)
    both["llm_judge"] = dict(
        SKIPPED_LOCKED_QUEUE["llm_judge"], source_failed=True, source_error="boom",
    )
    msg = qn.build_judge_cap_notice(both, now=now)
    assert "boom" in msg


# ── #410 round4 [Should]4: out_of_range_verdicts / reserved_batches が queue.json の
# llm_judge へ転記されず SessionStart から観測できなかった（judge_runner の戻り値と daily
# ログには出るが、evolve-daily-run の judge_summary 転記に含まれていなかった）。
# build_judge_summary（転記の単一ソース）でこれを埋め、build_judge_cap_notice にも
# out_of_range_verdicts>0 の通知を追加する（skipped_locked と同様の伝播）。


def test_build_judge_summary_includes_out_of_range_verdicts_and_reserved_batches():
    judge_result = {
        "unjudged_total": 10, "selected": 5, "capped": False, "corrections": 1,
        "call_failed": 0, "source_failed": False, "source_error": None,
        "skipped_locked": False, "out_of_range_verdicts": 2, "reserved_batches": 3,
    }
    summary = qn.build_judge_summary(judge_result)
    assert summary["out_of_range_verdicts"] == 2
    assert summary["reserved_batches"] == 3


def test_build_judge_summary_defaults_missing_fields_to_zero():
    """judge_result に欠けているキーがあっても KeyError にせず既定値で埋める
    （run_daily_judge の早期 return dict は将来キーが増減しても壊れないようにする）。
    """
    summary = qn.build_judge_summary({})
    assert summary["out_of_range_verdicts"] == 0
    assert summary["reserved_batches"] == 0
    assert summary["capped"] is False
    assert summary["source_failed"] is False


def test_build_judge_summary_none_input_returns_all_defaults():
    summary = qn.build_judge_summary(None)
    assert summary["unjudged_before"] == 0
    assert summary["out_of_range_verdicts"] == 0


OUT_OF_RANGE_QUEUE = {
    "generated_at": "2026-06-25T09:00:00Z",
    "threshold": 3,
    "tracked_total": 10,
    "queue": [],
    "llm_judge": {
        "unjudged_before": 5,
        "selected": 5,
        "capped": False,
        "corrections": 1,
        "call_failed": 0,
        "source_failed": False,
        "source_error": None,
        "skipped_locked": False,
        "out_of_range_verdicts": 3,
        "reserved_batches": 2,
    },
}


def test_judge_cap_notice_fires_when_out_of_range_verdicts_even_if_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_judge_cap_notice(OUT_OF_RANGE_QUEUE, now=now)
    assert msg is not None
    assert "3" in msg


def test_judge_cap_notice_silent_when_out_of_range_verdicts_zero_and_not_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert qn.build_judge_cap_notice(NOT_CAPPED_QUEUE, now=now) is None


def test_judge_cap_notice_capped_takes_priority_over_out_of_range_verdicts_message():
    """capped=True の方が運用上の優先度が高い（供給が上限で止まっている）ため、
    out_of_range_verdicts と同時発生時は capped メッセージを優先する。
    """
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    both = dict(CAPPED_QUEUE)
    both["llm_judge"] = dict(CAPPED_QUEUE["llm_judge"], out_of_range_verdicts=3)
    msg = qn.build_judge_cap_notice(both, now=now)
    assert "200" in msg  # capped メッセージ（selected 件数）が出る


# ── #442 契約4・5: 除外内訳（tracked外 / cutoff外）を既存サマリ行に1文足す ─────────
# 新しい通知系統は作らず、build_judge_summary への転記 + build_judge_cap_notice の既存
# メッセージへの追記だけで surface する（silence != evaluated）。


def test_build_judge_summary_includes_exclusion_fields():
    judge_result = {
        "unjudged_total": 10, "selected": 5, "capped": False, "corrections": 1,
        "call_failed": 0, "source_failed": False, "source_error": None,
        "skipped_locked": False, "out_of_range_verdicts": 0, "reserved_batches": 0,
        "excluded_untracked_total": 3,
        "excluded_untracked_by_pj": {"matsukaze-takashi": 2, "garbage-slug": 1},
        "excluded_before_cutoff_total": 4,
    }
    summary = qn.build_judge_summary(judge_result)
    assert summary["excluded_untracked_total"] == 3
    assert summary["excluded_untracked_by_pj"] == {"matsukaze-takashi": 2, "garbage-slug": 1}
    assert summary["excluded_before_cutoff_total"] == 4


def test_build_judge_summary_exclusion_fields_default_when_missing():
    """#442 以前の judge_result（早期 return dict が将来キー増減しても壊れない）。"""
    summary = qn.build_judge_summary({})
    assert summary["excluded_untracked_total"] == 0
    assert summary["excluded_untracked_by_pj"] == {}
    assert summary["excluded_before_cutoff_total"] == 0


def test_judge_cap_notice_appends_exclusion_suffix_when_capped():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    with_excl = dict(CAPPED_QUEUE)
    with_excl["llm_judge"] = dict(
        CAPPED_QUEUE["llm_judge"],
        excluded_untracked_total=5, excluded_before_cutoff_total=2,
    )
    msg = qn.build_judge_cap_notice(with_excl, now=now)
    assert msg is not None
    assert "tracked外5件" in msg
    assert "cutoff外2件" in msg


def test_judge_cap_notice_appends_exclusion_suffix_when_source_failed():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    with_excl = dict(SOURCE_FAILED_QUEUE)
    with_excl["llm_judge"] = dict(
        SOURCE_FAILED_QUEUE["llm_judge"], excluded_untracked_total=1,
    )
    msg = qn.build_judge_cap_notice(with_excl, now=now)
    assert "tracked外1件" in msg


def test_judge_cap_notice_appends_exclusion_suffix_when_skipped_locked():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    with_excl = dict(SKIPPED_LOCKED_QUEUE)
    with_excl["llm_judge"] = dict(
        SKIPPED_LOCKED_QUEUE["llm_judge"], excluded_before_cutoff_total=7,
    )
    msg = qn.build_judge_cap_notice(with_excl, now=now)
    assert "cutoff外7件" in msg


def test_judge_cap_notice_appends_exclusion_suffix_when_out_of_range():
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    with_excl = dict(OUT_OF_RANGE_QUEUE)
    with_excl["llm_judge"] = dict(
        OUT_OF_RANGE_QUEUE["llm_judge"], excluded_untracked_total=9,
    )
    msg = qn.build_judge_cap_notice(with_excl, now=now)
    assert "tracked外9件" in msg


def test_judge_cap_notice_no_exclusion_suffix_when_exclusions_are_zero():
    """除外が 0 件ならメッセージにノイズを足さない（既存メッセージのままの回帰防止）。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg = qn.build_judge_cap_notice(CAPPED_QUEUE, now=now)
    assert msg is not None
    assert "除外" not in msg


def test_judge_cap_notice_still_silent_when_not_capped_and_exclusions_present():
    """新しい通知系統は作らない契約: 既存メッセージが出ない（not capped・障害なし）局面
    では除外があっても沈黙のまま（既存サマリ行に足すだけで単独発火はしない）。
    """
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    with_excl = dict(NOT_CAPPED_QUEUE)
    with_excl["llm_judge"] = dict(
        NOT_CAPPED_QUEUE["llm_judge"], excluded_untracked_total=5,
    )
    assert qn.build_judge_cap_notice(with_excl, now=now) is None
