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
from evolve_revert._apply import apply_revert  # noqa: E402


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
    b64, _ = ids._compress_before_for_revert(before_text)
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
        "after_sha": ids._sha256(after_text),
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
    event_id = ids._revert_event_id("x1")
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


# ─── N1 apply 完了メッセージ ────────────────────────────────────────────────


def test_apply_success_message_includes_n1_notice(tmp_path, monkeypatch):
    canonical = _setup(tmp_path, monkeypatch)
    target = _make_target(tmp_path, "after\n")
    entry = _accept_entry("x1", "before\n", "after\n", target)
    _write_history(canonical, "proj", [entry])

    result = apply_revert("x1", slug="proj", dry_run=False)

    assert "戻しました" in result.message
    assert "また提案されることがあります" in result.message


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
