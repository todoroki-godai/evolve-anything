"""launchd plist 生成 + daily runner コマンド文字列（#80 Phase 1b）。

毎朝 1 回、`fleet ingest`（全 PJ 発話取込・ゼロ LLM）→ `fleet queue --json`（待ち判定）を
順に実行し、出力を `$CLAUDE_PLUGIN_DATA/evolve-queue.json` に保存する launchd ジョブを
記述する。実 launchctl 操作（load/unload）は `bin/evolve-daily-install` が行い、本モジュールは
副作用ゼロの純生成関数のみを提供する（単体テスト可能・LLM 非依存）。
"""
from pathlib import Path
from xml.sax.saxutils import escape

# launchd ジョブのラベル（plist ファイル名 = <LABEL>.plist）。
LAUNCHD_LABEL = "com.evolve-anything.daily"

# 既定実行時刻（毎日 09:00）。
DEFAULT_HOUR = 9
DEFAULT_MINUTE = 0


def plist_path() -> Path:
    """ユーザー LaunchAgents 配下の plist パスを返す。"""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def runner_path(plugin_root: str) -> str:
    """daily runner スクリプト（bin/evolve-daily-run）の絶対パスを返す。"""
    return str(Path(plugin_root) / "bin" / "evolve-daily-run")


def log_path(data_dir: str) -> str:
    """launchd ジョブのログパス（stdout/stderr 共通）を返す。"""
    return str(Path(data_dir) / "logs" / "evolve-daily.log")


def queue_json_path(data_dir: str) -> str:
    """evolve-queue.json（queue 出力の保存先・read 専用派生物）の絶対パスを返す。"""
    return str(Path(data_dir) / "evolve-queue.json")


def daily_command_str(fleet_bin: str, out_path: str) -> str:
    """runner が実行するシェルコマンド文字列を返す。

    `fleet ingest`（ゼロ LLM・全 PJ 発話取込）→ `fleet queue --json`（待ち判定）を順に走らせ、
    queue の JSON を `out_path`（evolve-queue.json）に保存する。ingest は失敗しても queue を
    走らせたいので `;` で繋ぐ（ingest が無くても古い取込で queue は判定できる）。
    """
    return (
        f"{fleet_bin} ingest ; "
        f"{fleet_bin} queue --json > {out_path}"
    )


def build_plist(
    plugin_root: str,
    data_dir: str,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
) -> str:
    """launchd plist の XML 文字列を生成する。

    ProgramArguments は `bin/evolve-daily-run`（runner スクリプト）を直接起動する。runner が
    内部で `daily_command_str` 相当の ingest→queue→保存を実行する。StartCalendarInterval で
    毎日 hour:minute に発火。stdout/stderr を `log_path` に集約しエラー時のスタックを残す。
    """
    runner = runner_path(plugin_root)
    log = log_path(data_dir)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{escape(LAUNCHD_LABEL)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{escape(runner)}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CLAUDE_PLUGIN_DATA</key>
        <string>{escape(data_dir)}</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{int(hour)}</integer>
        <key>Minute</key>
        <integer>{int(minute)}</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{escape(log)}</string>
    <key>StandardErrorPath</key>
    <string>{escape(log)}</string>
</dict>
</plist>
"""
