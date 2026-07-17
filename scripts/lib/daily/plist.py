"""launchd plist 生成 + daily runner コマンド文字列（#80 Phase 1b）。

毎朝 1 回、`fleet ingest`（全 PJ 発話取込・ゼロ LLM）→ `fleet queue --json`（待ち判定）を
順に実行し、出力を `$CLAUDE_PLUGIN_DATA/evolve-queue.json` に保存する launchd ジョブを
記述する。実 launchctl 操作（load/unload）は `bin/evolve-daily-install` が行い、本モジュールは
副作用ゼロの純生成関数のみを提供する（単体テスト可能・LLM 非依存）。
"""
import os
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
    python_exe: str = "",
    extra_path_dirs: "tuple[str, ...]" = (),
) -> str:
    """launchd plist の XML 文字列を生成する。

    ProgramArguments は `bin/evolve-daily-run`（runner スクリプト）を起動する。`python_exe` を
    渡すと `[python_exe, runner]` の順で並べ、runner の `#!/usr/bin/env python3` シェバンを迂回して
    そのインタプリタで直接実行する（launchd の PATH 先頭 /usr/bin の古い Python を拾って
    `str | None` 等の新記法が import 時 TypeError で死ぬのを防ぐ）。加えて EnvironmentVariables に
    `PATH`（python_exe の dir を先頭付与）を設定し、runner が bare パスで spawn する子プロセス
    （evolve-fleet の `#!/usr/bin/env python3`）も同じ Python に解決させる。空なら ProgramArguments
    は runner のみ。`extra_path_dirs` は python dir の後に追加する子ツールの dir
    （gh 等。launchd 最小 PATH に無い homebrew ツールを install 時検出で焼き込む・#196）で、
    python_exe 非依存に単独でも PATH に入る。python dir も extra も無ければ PATH 注入なし。
    StartCalendarInterval で毎日 hour:minute に発火。stdout/stderr を `log_path` に集約し
    エラー時のスタックを残す。
    """
    runner = runner_path(plugin_root)
    log = log_path(data_dir)
    program_args = [python_exe, runner] if python_exe else [runner]
    args_xml = "\n".join(f"        <string>{escape(a)}</string>" for a in program_args)

    env_entries = [("CLAUDE_PLUGIN_DATA", data_dir)]
    # launchd の既定 PATH は /usr/bin:/bin:... で homebrew を含まない。python_exe の dir を
    # 先頭に足し、子プロセスの `env python3` を pin した Python と同 dir に解決させる。
    # extra_path_dirs（gh 等の子ツール dir）はその後ろ・system パスの前。重複（python dir・
    # system パス含む）は除去。bare 名の python_exe は dirname が空になり、空セグメント＝
    # カレントディレクトリ優先の PATH インジェクションを焼き込むことになるため除外する。
    system_dirs = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
    py_dir = os.path.dirname(python_exe) if python_exe else ""
    path_dirs = []
    for d in (py_dir, *extra_path_dirs):
        if d and d not in path_dirs and d not in system_dirs:
            path_dirs.append(d)
    if path_dirs:
        env_entries.append(("PATH", ":".join(path_dirs + list(system_dirs))))
    env_xml = "\n".join(
        f"        <key>{escape(k)}</key>\n        <string>{escape(v)}</string>"
        for k, v in env_entries
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{escape(LAUNCHD_LABEL)}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>EnvironmentVariables</key>
    <dict>
{env_xml}
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
