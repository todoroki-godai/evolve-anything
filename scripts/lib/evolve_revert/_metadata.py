"""evolve_revert._metadata — apply のメタデータ契約（#402 段階3 §2 手順4 / C16-C24）。

mode / uid-gid / xattr / file flags / hardlink / ACL の検出・比較・override 判定。

xattr の実測訂正（v2 round4 tacchi [Must]）: ``os.listxattr`` は **Linux 限定 API** で
macOS の CPython には存在しない（実測 ``'listxattr' in dir(os)`` → ``False``）。macOS は
``/usr/bin/xattr`` subprocess で検出する（CLI・hot path でないため subprocess は許容。
LLM 呼び出しではないので単体テストの mock 対象外だが、環境依存の分岐は monkeypatch で
両方を検証する）。fd 経由の検出は ``/dev/fd/<fd>`` を subprocess へ渡す際に
``pass_fds=[fd]`` が必須（実測: 無指定だと子プロセスに fd が継承されず
``Bad file descriptor`` で失敗する）。
"""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Optional, Union

_HAS_OS_LISTXATTR = hasattr(os, "listxattr")
_XATTR_BIN = "/usr/bin/xattr"
_XATTR_BIN_EXISTS = Path(_XATTR_BIN).exists()


@dataclass(frozen=True)
class XattrProbe:
    """xattr 検出の結果。

    capable: 検出手段そのものが環境にあるか（``os.listxattr`` か ``/usr/bin/xattr`` の
             いずれか）。両方無ければ False（ACL と同じ「検査せず表示」対象・C19）。
    names:   capable かつ検出成功時のみ値を持つ（xattr 名の集合）。
    failed:  capable だが実行が失敗した（権限不足・subprocess 異常終了）。fail-closed
             で拒否する対象（override 不可・C19/C24）。
    """

    capable: bool
    names: Optional[FrozenSet[str]]
    failed: bool


def _parse_xattr_bin_output(text: str) -> FrozenSet[str]:
    return frozenset(line.strip() for line in text.splitlines() if line.strip())


def probe_xattrs_path(path: Path) -> XattrProbe:
    """path（symlink 非追従）の xattr を検出する。手順2・手順4 の初回検査用。"""
    if _HAS_OS_LISTXATTR:
        try:
            names = os.listxattr(str(path), follow_symlinks=False)
        except OSError:
            return XattrProbe(capable=True, names=None, failed=True)
        return XattrProbe(capable=True, names=frozenset(names), failed=False)
    if _XATTR_BIN_EXISTS:
        try:
            result = subprocess.run(
                [_XATTR_BIN, str(path)], capture_output=True, text=True, check=True
            )
        except (subprocess.CalledProcessError, OSError):
            return XattrProbe(capable=True, names=None, failed=True)
        return XattrProbe(capable=True, names=_parse_xattr_bin_output(result.stdout), failed=False)
    return XattrProbe(capable=False, names=None, failed=False)


def probe_xattrs_fd(fd: int) -> XattrProbe:
    """開いた fd の xattr を検出する（replace 直前の再検証用・C23: パス経由で
    stat し直さず、保持している fd と同じ対象を見る）。"""
    if _HAS_OS_LISTXATTR:
        try:
            names = os.listxattr(fd, follow_symlinks=False)
        except OSError:
            return XattrProbe(capable=True, names=None, failed=True)
        return XattrProbe(capable=True, names=frozenset(names), failed=False)
    if _XATTR_BIN_EXISTS:
        try:
            # 実測: /dev/fd/<fd> を subprocess へ渡すには pass_fds が必須（無指定だと
            # close_fds=True の既定で子プロセスに fd が継承されず Bad file descriptor）。
            result = subprocess.run(
                [_XATTR_BIN, f"/dev/fd/{fd}"],
                capture_output=True, text=True, check=True, pass_fds=[fd],
            )
        except (subprocess.CalledProcessError, OSError):
            return XattrProbe(capable=True, names=None, failed=True)
        return XattrProbe(capable=True, names=_parse_xattr_bin_output(result.stdout), failed=False)
    return XattrProbe(capable=False, names=None, failed=False)


@dataclass(frozen=True)
class MetadataSnapshot:
    """apply 対象ファイル1点のメタデータスナップショット（手順2の観測・手順4の再検証
    いずれもこの型で表す）。"""

    dev: int
    ino: int
    mode: int  # stat.S_IMODE 済み（種別ビットを含まない）
    is_regular: bool
    uid: int
    gid: int
    nlink: int
    xattr: XattrProbe
    flags: Optional[int]  # None は st_flags 非対応環境（検査スキップ）
    flags_supported: bool


def _snapshot_from_stat(st: os.stat_result, xattr: XattrProbe) -> MetadataSnapshot:
    flags_supported = hasattr(st, "st_flags")
    return MetadataSnapshot(
        dev=st.st_dev,
        ino=st.st_ino,
        mode=stat.S_IMODE(st.st_mode),
        is_regular=stat.S_ISREG(st.st_mode),
        uid=st.st_uid,
        gid=st.st_gid,
        nlink=st.st_nlink,
        xattr=xattr,
        flags=(st.st_flags if flags_supported else None),  # type: ignore[attr-defined]
        flags_supported=flags_supported,
    )


def snapshot_from_path(path: Union[str, Path]) -> MetadataSnapshot:
    """path 経由（symlink 非追従）のスナップショット。fd をまだ保持していない初回検査用。"""
    p = Path(path)
    st = os.lstat(p)
    return _snapshot_from_stat(st, probe_xattrs_path(p))


def snapshot_from_fd(fd: int) -> MetadataSnapshot:
    """開いた fd 経由のスナップショット。replace 直前の再検証は必ずこちらを使う
    （C23: source は検査中ずっと fd を保持し、比較にも同じ fd を使う）。"""
    st = os.fstat(fd)
    return _snapshot_from_stat(st, probe_xattrs_fd(fd))


# ─── drift 検出（C22: replace 直前の再検証・identity/regular/nlink/mode/uid-gid/ ──
# ─── xattr/flags が手順2の観測と一致するか。1つでも食い違えば replace しない）───

DRIFT_REASON_IDENTITY = "identity_changed"
DRIFT_REASON_NOT_REGULAR = "not_regular_file"
DRIFT_REASON_HARDLINK = "hardlink"
DRIFT_REASON_MODE = "mode_changed"
DRIFT_REASON_OWNER = "owner_changed"
DRIFT_REASON_XATTR = "xattr_changed"
DRIFT_REASON_XATTR_DETECT_FAILED = "xattr_detect_failed"
DRIFT_REASON_FLAGS = "flags_changed"


def detect_drift(initial: MetadataSnapshot, current: MetadataSnapshot) -> Optional[str]:
    """``initial``（手順2の観測）と ``current``（replace 直前の再検証）を比較する。

    差異が無ければ ``None``。content sha の再検証（項目4）は呼び出し側（``_apply.py``）
    が別途行う（このモジュールはファイル内容を読まない）。
    """
    if (initial.dev, initial.ino) != (current.dev, current.ino):
        return DRIFT_REASON_IDENTITY
    if not current.is_regular:
        return DRIFT_REASON_NOT_REGULAR
    if current.nlink != 1:
        return DRIFT_REASON_HARDLINK
    if initial.mode != current.mode:
        return DRIFT_REASON_MODE
    if (initial.uid, initial.gid) != (current.uid, current.gid):
        return DRIFT_REASON_OWNER
    # xattr: 検出不能（両側とも capable=False）なら比較しない（ACL と同じ扱い・C19）。
    # 検出手段はあるが実行が失敗（failed=True）は fail-closed で drift 扱い（非 override）。
    if initial.xattr.failed or current.xattr.failed:
        return DRIFT_REASON_XATTR_DETECT_FAILED
    if initial.xattr.capable and current.xattr.capable:
        if initial.xattr.names != current.xattr.names:
            return DRIFT_REASON_XATTR
    if initial.flags_supported and current.flags_supported:
        if initial.flags != current.flags:
            return DRIFT_REASON_FLAGS
    return None


# ─── loss 分類（§2 手順4 override 境界: 手順2 の初回検査で既に存在していた損失の ──
# ─── みが --allow-metadata-loss で override 可。観測後の変化・検査失敗・hardlink ──
# ─── は上の detect_drift 側で先に非 override 拒否になる・C24）───────────────


@dataclass(frozen=True)
class LossReport:
    """apply（復元）によって失われるメタデータの分類（dry-run 表示・override 判定用）。

    owner/xattr/flags: True なら ``--allow-metadata-loss`` が無い限り拒否する対象。
    acl_not_checked:   常に True（ACL は検出しない。検出できないものを理由に拒否は
                       しないが、dry-run には必ず明示表示する・C21）。
    xattr_not_checked: xattr 検出手段そのものが環境に無い場合 True（拒否理由にしない
                       が明示表示はする・C19）。
    """

    owner: bool
    xattr: bool
    flags: bool
    acl_not_checked: bool = True
    xattr_not_checked: bool = False

    @property
    def blocking(self) -> bool:
        return self.owner or self.xattr or self.flags


def preview_losses(source: MetadataSnapshot) -> LossReport:
    """dry-run 用の**近似** loss preview（C25）。

    正確な判定（``classify_losses``）は実際に temp を作って fstat 突合しないと出せない
    ——temp の group は親ディレクトリの setgid 等の影響を受けるため「元ファイルの
    uid/gid が実行ユーザーと同じか」だけでは replace 後の所有権保持を保証できない
    （§2 手順4）。dry-run は temp へゼロ書込の契約（C28）のため、ここでは実行ユーザーの
    euid/egid との比較という**近似ヒューリスティック**で代用する。xattr は「何が
    OS 自動付与か」を temp 無しには特定できないため、存在する xattr を保守的に
    全て「失われる可能性あり」として報告する（過小評価より過大評価を選ぶ）。
    """
    owner = (source.uid, source.gid) != (os.geteuid(), os.getegid())
    xattr_not_checked = not source.xattr.capable
    xattr = bool(source.xattr.capable and source.xattr.names)
    flags = bool(source.flags) if source.flags_supported else False
    return LossReport(owner=owner, xattr=xattr, flags=flags, xattr_not_checked=xattr_not_checked)


def classify_losses(source: MetadataSnapshot, temp: MetadataSnapshot) -> LossReport:
    """``source``（drift 検証済み・手順2の観測と不変であることが確認済み）と、
    実際に作った ``temp`` を突合して apply が失うメタデータを分類する（C16-C21）。

    mode は呼び出し側が temp 作成時に明示的に引き継ぐ契約なので、ここでは loss 対象に
    含めない（mode の不一致は ``detect_drift`` 相当の内部不整合として呼び出し側が
    別途扱う）。
    """
    owner = (temp.uid, temp.gid) != (source.uid, source.gid)

    xattr_not_checked = not (source.xattr.capable and temp.xattr.capable)
    xattr = False
    if not xattr_not_checked:
        missing_from_temp = (source.xattr.names or frozenset()) - (temp.xattr.names or frozenset())
        xattr = bool(missing_from_temp)

    flags = bool(source.flags) if source.flags_supported else False

    return LossReport(
        owner=owner, xattr=xattr, flags=flags, xattr_not_checked=xattr_not_checked
    )
