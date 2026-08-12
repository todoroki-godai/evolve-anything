"""evolve_revert._target — apply 対象パスの解決 + 安全検査（#402 段階3 §2 手順2）。

PR-1 は scope / ``relative_path`` を字句的絶対パス（symlink 非追従）で記録済み。
apply 側は ``repo root + relative_path`` / ``global root + relative_path`` で解決し
直す。**最終要素の lstat regular-file 判定**（symlink 自体を replace するのを防ぐ）
と、**解決後の実体が root 配下であること**（親ディレクトリ symlink 経由の脱出を防ぐ）
を**別々の検査**として実施する（C2）。``st_nlink != 1`` は conflict として拒否する
（hardlink では対象 pathname だけが新 inode になり、他リンクは after 内容のまま残る
ため・M5・C3）。
"""
from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from evolve_decision_ids import global_skills_root

REASON_UNSUPPORTED_SCOPE = "unsupported_scope"
REASON_MISSING_REPO_ID = "missing_repo_id"
REASON_MISSING_RELATIVE_PATH = "missing_relative_path"
REASON_NOT_FOUND = "not_found"
REASON_NOT_REGULAR_FILE = "not_regular_file"
REASON_ESCAPES_ROOT = "escapes_root"
REASON_HARDLINK = "hardlink"


@dataclass(frozen=True)
class TargetResolution:
    """``resolve_target`` の結果。

    path:  解決した絶対パス（``ok`` が False でも、解決自体はできた場合は返す——
           診断・メッセージ表示用。scope/repo_id/relative_path が欠落していて root
           すら決められない場合は ``None``）。
    ok:    apply を続行してよいか（regular file・root 配下・nlink==1 の全てを満たす）。
    reason: ``ok`` が False の理由コード（上記 ``REASON_*`` のいずれか）。
    nlink: 判定できた場合の ``st_nlink``（hardlink 判定の診断・メッセージ表示用）。
    """

    path: Optional[Path]
    ok: bool
    reason: Optional[str]
    nlink: Optional[int] = None


def resolve_target(entry: Dict[str, Any]) -> TargetResolution:
    """entry の scope/repo_id/relative_path から apply 対象パスを解決し安全検査する。

    - ``scope == "global"``: 正準 global skills root（``~/.claude/skills``・
      ``evolve_decision_ids.global_skills_root`` と同一ソース）+ root 相対パス
    - ``scope == "project"``: entry の ``repo_id``（git-common-dir の親 = 本体 repo
      root）+ root 相対パス
    - それ以外（``None`` 等）: ``REASON_UNSUPPORTED_SCOPE``
    """
    scope = entry.get("scope")
    relative_path = entry.get("relative_path")

    if scope == "global":
        root = global_skills_root()
    elif scope == "project":
        repo_id = entry.get("repo_id")
        if not repo_id:
            return TargetResolution(None, False, REASON_MISSING_REPO_ID)
        root = Path(repo_id)
    else:
        return TargetResolution(None, False, REASON_UNSUPPORTED_SCOPE)

    if not relative_path:
        return TargetResolution(None, False, REASON_MISSING_RELATIVE_PATH)

    target = root / relative_path

    # 最終要素の lstat regular-file 判定（symlink 自体を replace するのを防ぐ）。
    # lstat は最終要素だけを非追従とする POSIX 意味論のため、中間ディレクトリの
    # symlink はここでは検出できない（下の containment 検査が別途要る・C2）。
    try:
        st = target.lstat()
    except OSError:
        return TargetResolution(target, False, REASON_NOT_FOUND)
    if not stat.S_ISREG(st.st_mode):
        return TargetResolution(target, False, REASON_NOT_REGULAR_FILE)

    # 解決後の実体が root 配下であることを別検査で確認（親ディレクトリ symlink 経由の
    # 脱出を防ぐ）。``resolve()`` は symlink を辿るため、字句的パス判定（PR-1 の
    # ``lexical_absolute``）とは役割が異なる——ここは「実体がどこにあるか」を見る。
    try:
        real_root = root.resolve()
        real_target = target.resolve()
        real_target.relative_to(real_root)
    except (OSError, ValueError):
        return TargetResolution(target, False, REASON_ESCAPES_ROOT, nlink=st.st_nlink)

    if st.st_nlink != 1:
        return TargetResolution(target, False, REASON_HARDLINK, nlink=st.st_nlink)

    return TargetResolution(target, True, None, nlink=st.st_nlink)
