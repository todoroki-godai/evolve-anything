"""pj_slug — PJ slug 導出の単一ソース（#492）。

背景: slug 導出が2系統に分裂していた。
  - (a) ``optimize_history_store.resolve_slug``: ``git --git-common-dir`` の親 basename
        （authoritative・worktree から呼んでも本体 repo 名に正規化・subprocess あり）
  - (b) ``utterance_archive.extractor.pj_slug_from_cwd``: ``/.claude/worktrees/`` で
        切る文字列処理（高速・subprocess なし）
同一ストアの read/write で別方式が混ざると worktree 環境で slug が食い違い、
書いたレコードを読めない時限式 silent mismatch を生む（pitfall_worktree_slug_show_toplevel / #440）。

本モジュールがその恒久解（単一関数化）:
  - ``resolve_pj_slug(path_or_cwd)``: authoritative。git-common-dir があれば親 basename、
    git 不可（repo 外 / git 未インストール）なら文字列フォールバック（``pj_slug_fast``）。
  - ``pj_slug_fast(path)``: 文字列処理のみ。hot path（hooks）はこちらを使う（毎発火 hook で
    subprocess 禁止 — pitfall_hot_hook_eager_import / hot hook レイテンシ）。

既存2関数（``resolve_slug`` / ``pj_slug_from_cwd``）は本モジュールの thin wrapper に寄せ、
後方互換 re-export を維持する（呼び出し元の一斉書き換えはしない・段階移行）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Union

# git repo 外（slug 解決不能）の保全先。calibration 母集団からは除外される。
# optimize_history_store.UNATTRIBUTED_SLUG と同値（後方互換のため重複定義）。
UNATTRIBUTED_SLUG = "_unattributed"

# worktree セッションを本体 repo に帰属させるためのマーカー（path 中で切る位置）。
# utterance_archive.extractor._WORKTREE_MARKER と同値（後方互換のため重複定義）。
_WORKTREE_MARKER = "/.claude/worktrees/"


def pj_slug_fast(path: Optional[Union[str, Path]]) -> Optional[str]:
    """文字列処理のみで worktree 安全な pj_slug を導出する（hot path 用・subprocess なし）。

    1. path に ``/.claude/worktrees/`` が含まれればそこで切って本体側パスへ正規化
       （worktree セッションを main repo に帰属させる）
    2. pj_slug = 正規化後パスの basename

    path が None / 空なら None（呼び出し側が fallback する）。
    ``git rev-parse`` を呼ばないため、毎発火 hook から安全に使える。
    """
    if not path:
        return None
    s = str(path)
    marker_idx = s.find(_WORKTREE_MARKER)
    if marker_idx != -1:
        s = s[:marker_idx]  # 本体 repo root まで切り詰め
    base = Path(s).name
    return base or None


def resolve_pj_slug(path_or_cwd: Optional[Union[str, Path]] = None) -> str:
    """authoritative な pj_slug を返す（git-common-dir 親 basename・worktree 安全）。

    解決順:
      1. ``git rev-parse --git-common-dir`` で本体 repo の .git を取り、その親 basename。
         worktree から呼んでも本体 slug に正規化される（最も正確）。
      2. git 不可（repo 外 / git 未インストール / OS エラー）のフォールバックは **path に
         worktree マーカー（``/.claude/worktrees/``）がある場合のみ** ``pj_slug_fast`` で
         本体 slug に正規化する。worktree 由来の path が git なしでも本体 repo に帰属できる
         ようにするための限定フォールバックである。
      3. git 不可 かつ worktree マーカー無しの素の dir は ``UNATTRIBUTED_SLUG`` を返す
         （旧 ``resolve_slug`` のセマンティクスを温存 — calibration 母集団からの除外を壊さない）。

    ``path_or_cwd`` が None のときは現在の cwd（``Path.cwd()``）を使う。
    """
    cwd_path = Path(path_or_cwd) if path_or_cwd is not None else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd_path),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        out = ""

    if out:
        common_dir = Path(out)
        if not common_dir.is_absolute():
            common_dir = (cwd_path / common_dir).resolve()
        # common_dir は本体 repo の .git（または bare repo path）。親が repo root。
        slug = common_dir.parent.name
        if slug:
            return slug

    # git 不可: worktree マーカーがある path のみ文字列フォールバックで本体 slug に正規化。
    # マーカー無しの素の dir は _unattributed（旧 resolve_slug のセマンティクス温存）。
    if _WORKTREE_MARKER in str(cwd_path):
        fast = pj_slug_fast(cwd_path)
        if fast:
            return fast
    return UNATTRIBUTED_SLUG
