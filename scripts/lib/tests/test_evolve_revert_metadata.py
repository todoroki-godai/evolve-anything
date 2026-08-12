"""evolve_revert._metadata のユニットテスト（#402 段階3 §2 手順4 / C16-C24）。

メタデータ契約（mode/uid-gid/xattr/flags/hardlink/ACL）の検出・比較・override 判定。
決定論・LLM 非依存（``/usr/bin/xattr`` の subprocess は LLM でないため mock 対象外・
実 subprocess を使う。ただし CI/環境非依存にするため capable=False 環境も別途 mock で
カバーする）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_revert._metadata as md  # noqa: E402


# ─── xattr 検出（C18/C19: macOS=/usr/bin/xattr subprocess・Linux=os.listxattr）──


def test_probe_xattrs_path_detects_names_on_this_machine(tmp_path):
    """実測（darwin）: os.listxattr は無く /usr/bin/xattr は有る。新規ファイルにも
    com.apple.provenance 等が自動付与されうるため、値そのものは断定せず capable のみ
    固定する（環境依存）。"""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    probe = md.probe_xattrs_path(f)
    assert probe.capable is True
    assert probe.failed is False
    assert probe.names is not None  # 空集合の可能性はあるが None ではない


def test_probe_xattrs_path_not_capable_when_no_detection_means(tmp_path, monkeypatch):
    """C19: 検出手段そのものが環境に無い（両手段とも不可）→ ACL と同じ「検査せず表示」。"""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(md, "_HAS_OS_LISTXATTR", False)
    monkeypatch.setattr(md, "_XATTR_BIN_EXISTS", False)

    probe = md.probe_xattrs_path(f)

    assert probe.capable is False
    assert probe.names is None
    assert probe.failed is False


def test_probe_xattrs_path_fails_closed_when_subprocess_errors(tmp_path, monkeypatch):
    """C19: 検出手段はあるが実行が失敗（権限不足・subprocess 異常終了）→ fail-closed。"""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(md, "_HAS_OS_LISTXATTR", False)
    monkeypatch.setattr(md, "_XATTR_BIN_EXISTS", True)

    def _boom(*_a, **_kw):
        raise subprocess.CalledProcessError(1, ["xattr"])

    monkeypatch.setattr(md.subprocess, "run", _boom)

    probe = md.probe_xattrs_path(f)

    assert probe.capable is True
    assert probe.failed is True
    assert probe.names is None


def test_probe_xattrs_path_uses_os_listxattr_when_available(tmp_path, monkeypatch):
    """Linux 相当の分岐（macOS では os.listxattr が無いため mock で分岐を検証する）。"""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(md, "_HAS_OS_LISTXATTR", True)
    monkeypatch.setattr(os, "listxattr", lambda path, **kw: ["user.foo"], raising=False)

    probe = md.probe_xattrs_path(f)

    assert probe.capable is True
    assert probe.names == frozenset({"user.foo"})


def test_probe_xattrs_fd_matches_path_probe_on_real_file_with_user_xattr(tmp_path):
    """実ファイル1回通し（learning_synthetic_fixture_false_confidence 対応）。

    ``/usr/bin/xattr -w`` で実際にユーザー由来 xattr を1つ設定し、path 経由・fd 経由の
    両方の検出が同じ名前集合を返すことを確認する（``/dev/fd/<fd>`` の subprocess 渡しは
    ``pass_fds`` 無指定だと ``Bad file descriptor`` になることを実測済み・docstring 参照）。
    """
    if not md._XATTR_BIN_EXISTS and not md._HAS_OS_LISTXATTR:
        import pytest

        pytest.skip("この環境に xattr 検出手段が無い")
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    if md._XATTR_BIN_EXISTS:
        subprocess.run([md._XATTR_BIN, "-w", "user.evolve_revert_test", "v", str(f)], check=True)
    else:
        os.setxattr(str(f), "user.evolve_revert_test", b"v")  # type: ignore[attr-defined]

    path_probe = md.probe_xattrs_path(f)
    fd = os.open(str(f), os.O_RDONLY)
    try:
        fd_probe = md.probe_xattrs_fd(fd)
    finally:
        os.close(fd)

    assert path_probe.capable is True
    assert "user.evolve_revert_test" in (path_probe.names or frozenset())
    assert fd_probe.names == path_probe.names


# ─── metadata snapshot ────────────────────────────────────────────────────


def test_snapshot_from_path_captures_core_fields(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, 0o640)

    snap = md.snapshot_from_path(f)

    st = f.lstat()
    assert snap.dev == st.st_dev
    assert snap.ino == st.st_ino
    assert snap.mode == 0o640
    assert snap.is_regular is True
    assert snap.uid == st.st_uid
    assert snap.gid == st.st_gid
    assert snap.nlink == 1


def test_snapshot_from_fd_matches_snapshot_from_path(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    fd = os.open(str(f), os.O_RDONLY)
    try:
        snap_fd = md.snapshot_from_fd(fd)
    finally:
        os.close(fd)
    snap_path = md.snapshot_from_path(f)
    assert (snap_fd.dev, snap_fd.ino, snap_fd.mode, snap_fd.uid, snap_fd.gid) == (
        snap_path.dev, snap_path.ino, snap_path.mode, snap_path.uid, snap_path.gid,
    )


def test_snapshot_flags_supported_on_this_machine(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    snap = md.snapshot_from_path(f)
    # darwin は st_flags を持つ（実測）。新規ファイルの既定 flags は 0。
    assert snap.flags_supported is True
    assert snap.flags == 0


# ─── drift 検出（C22: replace 直前の再検証・6項目）──────────────────────────


def _snap(**overrides):
    base = dict(
        dev=1, ino=1, mode=0o644, is_regular=True, uid=501, gid=20, nlink=1,
        xattr=md.XattrProbe(capable=True, names=frozenset(), failed=False),
        flags=0, flags_supported=True,
    )
    base.update(overrides)
    return md.MetadataSnapshot(**base)


def test_detect_drift_none_when_identical():
    assert md.detect_drift(_snap(), _snap()) is None


def test_detect_drift_identity_changed():
    assert md.detect_drift(_snap(), _snap(ino=2)) == md.DRIFT_REASON_IDENTITY


def test_detect_drift_not_regular():
    assert md.detect_drift(_snap(), _snap(is_regular=False)) == md.DRIFT_REASON_NOT_REGULAR


def test_detect_drift_hardlink():
    assert md.detect_drift(_snap(), _snap(nlink=2)) == md.DRIFT_REASON_HARDLINK


def test_detect_drift_mode_changed():
    assert md.detect_drift(_snap(), _snap(mode=0o600)) == md.DRIFT_REASON_MODE


def test_detect_drift_owner_changed():
    assert md.detect_drift(_snap(), _snap(uid=999)) == md.DRIFT_REASON_OWNER


def test_detect_drift_xattr_changed():
    current = _snap(xattr=md.XattrProbe(capable=True, names=frozenset({"user.x"}), failed=False))
    assert md.detect_drift(_snap(), current) == md.DRIFT_REASON_XATTR


def test_detect_drift_xattr_not_compared_when_either_side_incapable():
    """検出不能な環境では xattr の drift 判定自体をスキップする（ACL と同じ扱い）。"""
    incapable = md.XattrProbe(capable=False, names=None, failed=False)
    a = _snap(xattr=incapable)
    b = _snap(xattr=incapable)
    assert md.detect_drift(a, b) is None


def test_detect_drift_xattr_detection_failure_is_drift():
    """C19: 検出手段はあるが実行が失敗 → fail-closed（drift 扱いで拒否・override 不可）。"""
    failed = md.XattrProbe(capable=True, names=None, failed=True)
    assert md.detect_drift(_snap(), _snap(xattr=failed)) == md.DRIFT_REASON_XATTR_DETECT_FAILED


def test_detect_drift_flags_changed():
    assert md.detect_drift(_snap(), _snap(flags=1)) == md.DRIFT_REASON_FLAGS


def test_detect_drift_flags_not_compared_when_unsupported():
    unsupported = _snap(flags=None, flags_supported=False)
    assert md.detect_drift(unsupported, unsupported) is None


# ─── loss 分類（C16-C21/C24: 初回検査で既に存在していた損失のみ override 可）───


def test_classify_losses_no_loss_when_temp_matches_source():
    source = _snap()
    temp = _snap(mode=source.mode)  # temp は mode を引き継いでいる想定
    report = md.classify_losses(source, temp)
    assert report.blocking is False
    assert report.owner is False
    assert report.xattr is False
    assert report.flags is False


def test_classify_losses_owner_mismatch_is_loss():
    source = _snap(uid=501, gid=20)
    temp = _snap(uid=0, gid=0)  # 新規作成された temp は別 owner
    report = md.classify_losses(source, temp)
    assert report.owner is True
    assert report.blocking is True


def test_classify_losses_xattr_present_only_on_source_is_loss():
    source = _snap(xattr=md.XattrProbe(True, frozenset({"user.custom"}), False))
    temp = _snap(xattr=md.XattrProbe(True, frozenset(), False))
    report = md.classify_losses(source, temp)
    assert report.xattr is True


def test_classify_losses_xattr_present_on_both_is_not_loss():
    """C18: source − temp の差集合が空なら通す（OS 自動付与分は差集合に出ない）。"""
    both = md.XattrProbe(True, frozenset({"com.apple.provenance"}), False)
    source = _snap(xattr=both)
    temp = _snap(xattr=both)
    report = md.classify_losses(source, temp)
    assert report.xattr is False


def test_classify_losses_xattr_incapable_is_not_a_blocking_loss_but_is_surfaced():
    """C19/C21 と同型: 検出不能なら拒否理由にしない。ただし常に明示表示対象。"""
    incapable = md.XattrProbe(False, None, False)
    source = _snap(xattr=incapable)
    temp = _snap(xattr=incapable)
    report = md.classify_losses(source, temp)
    assert report.xattr is False
    assert report.xattr_not_checked is True
    assert report.blocking is False


def test_classify_losses_flags_nonzero_on_source_is_loss():
    source = _snap(flags=1)
    temp = _snap(flags=0)
    report = md.classify_losses(source, temp)
    assert report.flags is True


def test_classify_losses_acl_always_marked_not_checked():
    report = md.classify_losses(_snap(), _snap())
    assert report.acl_not_checked is True


# ─── dry-run 近似 preview（C25: temp を作らず source 単体から推定） ──────────


def test_preview_losses_owner_mismatch_vs_process_euid_egid():
    source = _snap(uid=os.geteuid() + 1, gid=os.getegid())
    report = md.preview_losses(source)
    assert report.owner is True


def test_preview_losses_owner_match_is_not_a_loss():
    source = _snap(uid=os.geteuid(), gid=os.getegid())
    report = md.preview_losses(source)
    assert report.owner is False


def test_preview_losses_any_xattr_present_is_conservatively_a_loss():
    source = _snap(
        uid=os.geteuid(), gid=os.getegid(),
        xattr=md.XattrProbe(True, frozenset({"com.apple.provenance"}), False),
    )
    report = md.preview_losses(source)
    assert report.xattr is True


def test_preview_losses_xattr_incapable_is_not_blocking():
    source = _snap(uid=os.geteuid(), gid=os.getegid(), xattr=md.XattrProbe(False, None, False))
    report = md.preview_losses(source)
    assert report.xattr is False
    assert report.xattr_not_checked is True
