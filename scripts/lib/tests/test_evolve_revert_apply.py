"""evolve_revert._apply のユニットテスト（#402 段階3 §2 手順3-5 / C4-C7, C16-C29）。

apply engine 本体: entry 検索 → 対象解決 → 3分岐（正常系/冪等/conflict）→ 復元 →
再検証 → revert イベント追記。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import pytest  # noqa: E402

import evolve_decision_ids as ids  # noqa: E402
import optimize_history_store as store  # noqa: E402
from evolve_revert._apply import apply_revert, detect_subsequent_change  # noqa: E402


def _write_history(dir_: Path, slug: str, records: list) -> None:
    oh = dir_ / "optimize_history"
    oh.mkdir(parents=True, exist_ok=True)
    (oh / f"{slug}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _setup(tmp_path, monkeypatch):
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    return canonical


def _make_target(tmp_path, content: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    target = repo / "SKILL.md"
    target.write_text(content, encoding="utf-8")
    return target


def _accept_entry(entry_id: str, before_text: str, after_text: str, target: Path, **overrides):
    b64, _ = ids.compress_before_for_revert(before_text)
    base = {
        "id": entry_id,
        "human_accepted": True,
        "skill_name": "my-skill",
        "revert_before_b64": b64,
        "revert_schema_version": ids.REVERT_SCHEMA_VERSION,
        "revert_encoding": ids.REVERT_ENCODING,
        "revert_generation": 0,
        "scope": "project",
        "repo_id": str(target.parent),
        "relative_path": target.name,
        "after_sha": ids.sha256(after_text),
    }
    base.update(overrides)
    return base


# ─── 正常系（== after_sha）───────────────────────────────────────────────


def test_normal_branch_restores_content_and_appends_event(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after-content\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is True
    assert result.branch == "normal"
    assert target.read_text(encoding="utf-8") == "before-content\n"
    events = store.load_revert_events("proj")
    assert len(events) == 1
    assert events[0]["reverted_entry_id"] == "x1"
    assert store.missing_revert_event_fields(events[0]) == []


def test_normal_branch_preserves_mode(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    os.chmod(target, 0o640)
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    apply_revert("x1", slug="proj", dry_run=False)

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_normal_branch_increments_revert_generation_from_entry_snapshot(tmp_path, monkeypatch):
    """revert_generation は entry 自身の（accept 時にスナップショットされた）値 + 1。
    ライブな履歴再スキャンに依存しない（冪等性のため・段階3 の設計判断）。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target, revert_generation=2)
    _write_history(canonical, "proj", [entry])

    apply_revert("x1", slug="proj", dry_run=False)

    events = store.load_revert_events("proj")
    assert events[0]["revert_generation"] == 3


def test_normal_branch_dry_run_writes_nothing(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    before_stat = target.stat()
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])
    lock_path = store.history_path("proj").with_name(store.history_path("proj").name + ".lock")

    result = apply_revert("x1", slug="proj", dry_run=True)

    assert result.ok is True
    assert result.branch == "normal"
    assert target.read_text(encoding="utf-8") == "after\n"  # 対象ファイル: ゼロ書込
    assert target.stat().st_mtime == before_stat.st_mtime
    assert not lock_path.exists()  # history lock sidecar: ゼロ書込
    assert store.load_revert_events("proj") == []  # history: ゼロ書込
    # temp: ゼロ書込（同ディレクトリに tmp 残骸が無い）
    leftovers = [p for p in target.parent.iterdir() if p.name.startswith(".SKILL.md.")]
    assert leftovers == []


def test_normal_branch_dry_run_includes_diff_summary(tmp_path, monkeypatch):
    """#469: dry-run の結果に revert 後の差分要約（+N/-M行）が含まれる。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after1\nafter2\n")
    entry = _accept_entry("x1", "before1\n", "after1\nafter2\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=True)

    assert result.ok is True
    assert result.branch == "normal"
    assert result.diff is not None
    assert result.diff["binary_or_undecodable"] is False
    assert result.diff["removed_lines"] == 1  # before1 が消える
    assert result.diff["added_lines"] == 2    # after1, after2 が追加される
    assert "変更行数" in result.message
    # 対象ファイル・history: 引き続きゼロ書込
    assert target.read_text(encoding="utf-8") == "after1\nafter2\n"
    assert store.load_revert_events("proj") == []


# ─── 冪等パス（== before_sha）─────────────────────────────────────────────


def test_idempotent_branch_appends_missing_event_when_absent(tmp_path, monkeypatch):
    """S7: 前回の中断（復元済み・イベント欠落）でも手動で before に戻した場合でも、
    どちらもイベントを追記して正式な revert とみなす。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "before-content\n")  # 既に before 状態
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is True
    assert result.branch == "idempotent"
    assert target.read_text(encoding="utf-8") == "before-content\n"  # 対象は触らない
    events = store.load_revert_events("proj")
    assert len(events) == 1


def test_idempotent_branch_is_true_noop_when_event_already_recorded(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "before-content\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    event_id = ids.revert_event_id("x1")
    revert_event = {
        "event_type": "revert",
        "reverted_entry_id": "x1",
        "revert_event_id": event_id,
        "revert_generation": 1,
        "scope": "project",
        "repo_id": str(target.parent),
        "relative_path": target.name,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "skill_name": "my-skill",
    }
    _write_history(canonical, "proj", [entry, revert_event])
    hist_path = store.history_path("proj")
    before_bytes = hist_path.read_bytes()

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is True
    assert result.branch == "idempotent"
    assert hist_path.read_bytes() == before_bytes  # 完全冪等: 何も書かない


def test_idempotent_branch_dry_run_writes_nothing(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "before-content\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=True)

    assert result.ok is True
    assert result.branch == "idempotent"
    assert store.load_revert_events("proj") == []


# ─── conflict（どちらでもない）─────────────────────────────────────────────


def test_conflict_branch_writes_nothing_and_returns_message(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "someone-else-changed-this\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is False
    assert result.branch == "conflict"
    assert "この差分は" in result.message
    assert "次アクション" in result.message
    assert target.read_text(encoding="utf-8") == "someone-else-changed-this\n"
    assert store.load_revert_events("proj") == []


def test_conflict_branch_message_includes_dump_before_and_apply_commands(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "diverged\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert "x1 --dump-before" in result.message
    assert "x1 --apply" in result.message


# ─── 未発見・schema gap ────────────────────────────────────────────────────


def test_entry_not_found(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    result = apply_revert("nope", slug="proj", dry_run=False)

    assert result.ok is False
    assert result.reason == "entry_not_found"


def test_before_unavailable_is_rejected(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    del entry["revert_before_b64"]
    entry["revert_unavailable_reason"] = ids.REVERT_REASON_BEFORE_TOO_LARGE
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is False
    assert result.reason == "before_unavailable"


def test_after_sha_missing_is_rejected(tmp_path, monkeypatch):
    """schema gap（段階3 で after_sha を追加する前の legacy entry 相当）への防御。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    del entry["after_sha"]
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is False
    assert result.reason == "after_sha_missing"


# ─── hardlink（M5）─────────────────────────────────────────────────────────


def test_hardlink_target_is_rejected(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    os.link(target, tmp_path / "other-link.md")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is False
    assert result.reason == "hardlink"
    assert "hardlink" in result.message
    assert "--allow-metadata-loss でも解除不可" in result.message


def test_hardlink_rejection_is_not_overridable(tmp_path, monkeypatch):
    """C3/C24: --allow-metadata-loss でも hardlink 拒否は解除できない（整合性破壊）。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    os.link(target, tmp_path / "other-link.md")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False, allow_metadata_loss=True)

    assert result.ok is False
    assert result.reason == "hardlink"
    assert target.read_text(encoding="utf-8") == "after\n"


# ─── メタデータ損失の拒否/override（C24）───────────────────────────────────


def test_metadata_loss_blocks_apply_without_override(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    # source の owner を実行ユーザーと異なる値に偽装する（実際に chown する権限は無い
    # ことが多いため snapshot_from_fd をパッチして「所有者不一致」を模擬する）。
    import evolve_revert._apply as apply_module
    from evolve_revert._metadata import XattrProbe, snapshot_from_fd as real_snapshot_from_fd

    def _fake_snapshot_from_fd(fd):
        snap = real_snapshot_from_fd(fd)
        return snap.__class__(
            dev=snap.dev, ino=snap.ino, mode=snap.mode, is_regular=snap.is_regular,
            uid=snap.uid + 1, gid=snap.gid, nlink=snap.nlink, xattr=snap.xattr,
            flags=snap.flags, flags_supported=snap.flags_supported,
        )

    monkeypatch.setattr(apply_module, "snapshot_from_fd", _fake_snapshot_from_fd)

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert result.ok is False
    assert result.reason == "metadata_loss"
    assert "所有者" in result.message
    assert "--allow-metadata-loss" in result.message
    assert target.read_text(encoding="utf-8") == "after\n"  # 置換していない
    assert store.load_revert_events("proj") == []


def test_metadata_loss_override_allows_apply(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    import evolve_revert._apply as apply_module
    from evolve_revert._metadata import snapshot_from_fd as real_snapshot_from_fd

    def _fake_snapshot_from_fd(fd):
        snap = real_snapshot_from_fd(fd)
        return snap.__class__(
            dev=snap.dev, ino=snap.ino, mode=snap.mode, is_regular=snap.is_regular,
            uid=snap.uid + 1, gid=snap.gid, nlink=snap.nlink, xattr=snap.xattr,
            flags=snap.flags, flags_supported=snap.flags_supported,
        )

    monkeypatch.setattr(apply_module, "snapshot_from_fd", _fake_snapshot_from_fd)

    result = apply_revert("x1", slug="proj", dry_run=False, allow_metadata_loss=True)

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "before\n"
    assert len(store.load_revert_events("proj")) == 1


def test_drift_between_initial_observation_and_reverify_is_not_overridable(tmp_path, monkeypatch):
    """C24: 「観測後の変化」は --allow-metadata-loss でも解除不可（初回検査で既に
    存在していた損失とは別分類）。初回スナップショットと再検証スナップショットの間で
    owner が変わるケースを1回目/2回目で異なる値を返す fake で模擬する。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    import evolve_revert._apply as apply_module
    from evolve_revert._metadata import snapshot_from_fd as real_snapshot_from_fd

    call_state = {"n": 0}

    def _drifting_snapshot_from_fd(fd):
        call_state["n"] += 1
        snap = real_snapshot_from_fd(fd)
        if call_state["n"] == 1:
            return snap  # 手順2 の観測: 変化なし
        # replace 直前の再検証: owner が変わっている（観測後の drift）。
        return snap.__class__(
            dev=snap.dev, ino=snap.ino, mode=snap.mode, is_regular=snap.is_regular,
            uid=snap.uid + 1, gid=snap.gid, nlink=snap.nlink, xattr=snap.xattr,
            flags=snap.flags, flags_supported=snap.flags_supported,
        )

    monkeypatch.setattr(apply_module, "snapshot_from_fd", _drifting_snapshot_from_fd)

    result = apply_revert("x1", slug="proj", dry_run=False, allow_metadata_loss=True)

    assert result.ok is False
    assert result.reason == "drift"
    assert target.read_text(encoding="utf-8") == "after\n"
    assert store.load_revert_events("proj") == []


def test_xattr_detection_failure_is_not_overridable(tmp_path, monkeypatch):
    """C19/C24: 検出手段はあるが実行が失敗（fail-closed）は --allow-metadata-loss でも
    解除不可（「検出不能（環境に手段が無い）」とは別分類）。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    import evolve_revert._apply as apply_module
    from evolve_revert._metadata import XattrProbe, snapshot_from_fd as real_snapshot_from_fd

    def _fake_snapshot_from_fd(fd):
        snap = real_snapshot_from_fd(fd)
        return snap.__class__(
            dev=snap.dev, ino=snap.ino, mode=snap.mode, is_regular=snap.is_regular,
            uid=snap.uid, gid=snap.gid, nlink=snap.nlink,
            xattr=XattrProbe(capable=True, names=None, failed=True),
            flags=snap.flags, flags_supported=snap.flags_supported,
        )

    monkeypatch.setattr(apply_module, "snapshot_from_fd", _fake_snapshot_from_fd)

    result = apply_revert("x1", slug="proj", dry_run=False, allow_metadata_loss=True)

    assert result.ok is False
    assert result.reason == "drift"
    assert target.read_text(encoding="utf-8") == "after\n"


def test_same_source_fd_used_for_initial_and_reverify_snapshots(tmp_path, monkeypatch):
    """C23: source は検査中ずっと fd を保持し、比較にも同じ fd を使う（パス経由で
    stat し直さない）。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    import evolve_revert._apply as apply_module
    from evolve_revert._metadata import snapshot_from_fd as real_snapshot_from_fd

    seen_fds: list = []

    def _spy_snapshot_from_fd(fd):
        seen_fds.append(fd)
        return real_snapshot_from_fd(fd)

    monkeypatch.setattr(apply_module, "snapshot_from_fd", _spy_snapshot_from_fd)

    apply_revert("x1", slug="proj", dry_run=False)

    assert len(seen_fds) == 2
    assert seen_fds[0] == seen_fds[1]


# ─── revert イベントは学習系に流さない（§8 N2・C30）─────────────────────────


def test_revert_event_does_not_write_to_weak_signals(tmp_path, monkeypatch):
    """#379 Step1 新設凍結中: revert イベントは optimize_history のみに append され、
    weak_signals 等の学習系チャネルには一切書き込まない（黙って流す実装を作らない）。"""
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    apply_revert("x1", slug="proj", dry_run=False)

    weak_signals_dir = store.DATA_DIR / "weak_signals"
    assert not weak_signals_dir.exists() or list(weak_signals_dir.iterdir()) == []


# ─── N1 apply 完了メッセージ ────────────────────────────────────────────────


def test_apply_success_message_includes_n1_notice(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert "戻しました" in result.message
    assert "また提案されることがあります" in result.message


# ─── detect_subsequent_change（§8.2 後続変更検知・--list 表示用・read-only）─────
#
# _apply.py:294-301 の3分岐判定（== after_sha / == before_sha / どちらでもない）を
# apply（書込）せず listing 時点で流用する。単一ソース: 新しい判定ロジックを
# 再実装しない。


def test_detect_subsequent_change_false_when_current_matches_after_sha(tmp_path, monkeypatch):
    """current == after_sha（採用直後から変わっていない）→ 戻せる（False）。"""
    _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after-content\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)

    assert detect_subsequent_change(entry) is False


def test_detect_subsequent_change_false_when_current_matches_before_sha(tmp_path, monkeypatch):
    """current == before_sha（既に手動で戻っている＝冪等）→ 戻せる（False）。"""
    _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "before-content\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)

    assert detect_subsequent_change(entry) is False


def test_detect_subsequent_change_true_when_current_matches_neither(tmp_path, monkeypatch):
    """current がどちらとも一致しない（後続で別の変更が入った）→ 戻せない（True）。"""
    _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "someone-else-changed-this\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)

    assert detect_subsequent_change(entry) is True


def test_detect_subsequent_change_is_read_only(tmp_path, monkeypatch):
    """判定は対象ファイル・history のいずれにも書込まない。"""
    _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "someone-else-changed-this\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    before_bytes = target.read_bytes()
    before_mtime = target.stat().st_mtime

    detect_subsequent_change(entry)

    assert target.read_bytes() == before_bytes
    assert target.stat().st_mtime == before_mtime
    leftovers = [p for p in target.parent.iterdir() if p.name.startswith(".SKILL.md.")]
    assert leftovers == []


def test_detect_subsequent_change_true_when_target_unresolvable(tmp_path, monkeypatch):
    """対象パスを解決できない場合は安全側（戻せない扱い）に倒す。"""
    _setup(tmp_path, monkeypatch)
    entry = {
        "id": "x1",
        "revert_before_b64": "eJw...",
        "after_sha": "deadbeef",
        "scope": "project",
        "repo_id": str(tmp_path / "repo"),
        "relative_path": "nope.md",
    }

    assert detect_subsequent_change(entry) is True


def test_detect_subsequent_change_true_when_after_sha_missing(tmp_path, monkeypatch):
    """判定材料（after_sha）が欠けている場合も安全側に倒す。"""
    _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after-content\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    del entry["after_sha"]

    assert detect_subsequent_change(entry) is True


def test_detect_subsequent_change_true_when_before_b64_missing(tmp_path, monkeypatch):
    """判定材料（revert_before_b64）が欠けている場合も安全側に倒す。"""
    _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "someone-else-changed-this\n")
    entry = _accept_entry("x1", "before-content\n", "after-content\n", target)
    del entry["revert_before_b64"]

    assert detect_subsequent_change(entry) is True


# ─── ロック（C4/C26: 手順3〜5 は同一 history lock 内）───────────────────────


def test_concurrent_dry_run_cannot_proceed_while_apply_holds_lock(tmp_path, monkeypatch):
    """ロック保持中に相手が進めないことを確認する（N プロセス同時実行でなく
    ロック保持で固定・learning_concurrency_test_by_lock_holding）。"""
    from rl_common.file_lock import file_lock

    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])
    lock_path = store.history_path("proj").with_name(store.history_path("proj").name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()

    box: dict = {}

    def _try_dry_run():
        box["result"] = apply_revert("x1", slug="proj", dry_run=True)

    with file_lock(lock_path):
        thread = threading.Thread(target=_try_dry_run, daemon=True)
        thread.start()
        thread.join(timeout=1)
        assert thread.is_alive(), "history lock 保持中に dry-run が進んでしまった"

    thread.join(timeout=10)
    assert not thread.is_alive(), "history lock 解放後も dry-run がハングした"
    assert box["result"].ok is True
