"""daily launchd ジョブの install / uninstall（#80 Phase 1b）。

`bin/evolve-daily-install` の本体ロジック。plist を生成・LaunchAgents へ書き、`launchctl load`
で登録する。`--uninstall` で `launchctl unload` + plist 削除。すべて冪等。

launchctl 呼び出しは `_launchctl`（subprocess）に集約し、単体テストで mock 可能にする。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from daily import plist as plist_mod
from daily.plist import LAUNCHD_LABEL


def _launchctl(subcommand: str, target: str) -> int:
    """`launchctl <subcommand> <target>` を実行し returncode を返す。

    load/unload はジョブ未登録・既登録のいずれでも非ゼロを返しうるが、冪等運用では致命でない。
    呼び出し側はログに残すだけで握りつぶす。
    """
    return subprocess.run(
        ["launchctl", subcommand, target],
        capture_output=True,
        text=True,
    ).returncode


def _child_tool_dirs() -> "tuple[str, ...]":
    """runner の子プロセスが使う外部ツール（gh 等）の dir を install 環境から検出する。

    launchd の最小 PATH は /opt/homebrew/bin を含まないため、icebox 集計（#194）の gh が
    FileNotFoundError で恒久 fail-open になる（#196）。install は対話シェルで走るので、
    その PATH で which した実体の dir を plist に焼き込む。見つからなければ空
    （icebox は fail-open のまま・install は成功する）。
    """
    dirs = []
    gh = shutil.which("gh")
    if gh:
        dirs.append(os.path.dirname(gh))
    return tuple(dirs)


def install(
    plugin_root: str,
    data_dir: str,
    hour: int = plist_mod.DEFAULT_HOUR,
    minute: int = plist_mod.DEFAULT_MINUTE,
    python_exe: str = "",
) -> int:
    """launchd ジョブを登録する（冪等）。

    - LaunchAgents dir を作成し plist を書く（既存なら上書き）。
    - 既存 plist があれば一度 unload してから load し直す（再登録・設定変更を反映）。
    - data_dir/logs を作りログ出力先を用意する。
    - `python_exe` を渡すと plist に焼く。空なら install を走らせている `sys.executable` を
      pin する（＝この install を叩いた Python で毎朝の runner を実行する）。launchd の PATH
      先頭 /usr/bin の古い Python を拾う事故を防ぐ。
    - 子ツール（gh 等）の dir を install 環境の which で検出し PATH に焼き込む（#196）。
    Returns: 0（plist 書き込み成功）。launchctl の非ゼロは握りつぶす（冪等運用）。
    """
    plist_file = plist_mod.plist_path()
    plist_file.parent.mkdir(parents=True, exist_ok=True)

    # ログ dir を用意（launchd が StandardOut/ErrorPath に書けるように）。
    Path(plist_mod.log_path(data_dir)).parent.mkdir(parents=True, exist_ok=True)

    # 既存ジョブがあれば一旦 unload（設定変更の反映・二重登録回避）。
    if plist_file.exists():
        _launchctl("unload", str(plist_file))

    body = plist_mod.build_plist(
        plugin_root=plugin_root,
        data_dir=data_dir,
        hour=hour,
        minute=minute,
        python_exe=python_exe or sys.executable,
        extra_path_dirs=_child_tool_dirs(),
    )
    plist_file.write_text(body, encoding="utf-8")

    _launchctl("load", str(plist_file))
    return 0


def uninstall() -> int:
    """launchd ジョブを撤去する（冪等）。

    plist があれば unload してから削除する。plist が無ければ no-op。
    Returns: 0。
    """
    plist_file = plist_mod.plist_path()
    if plist_file.exists():
        _launchctl("unload", str(plist_file))
        plist_file.unlink()
    return 0
