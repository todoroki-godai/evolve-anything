"""evolve_revert._render のユニットテスト（#402 段階3 §2 手順3 / C8-C13, C25, C29）。

利用者に見えるメッセージの生成。決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_revert._render as render  # noqa: E402
from evolve_revert._metadata import LossReport  # noqa: E402


# ─── hardlink 拒否（C12）───────────────────────────────────────────────────


def test_hardlink_rejection_states_nlink_and_no_override():
    message = render.render_hardlink_rejection(3)
    assert "nlink=3" in message
    assert "--allow-metadata-loss" in message
    assert "解除不可" in message


# ─── メタデータ拒否（C13）──────────────────────────────────────────────────


def test_metadata_loss_rejection_prints_concrete_allow_flag_command():
    losses = LossReport(owner=True, xattr=False, flags=False)
    message = render.render_metadata_loss_rejection("evdiff_abc", losses)
    assert "evdiff_abc" in message
    assert "--allow-metadata-loss" in message
    assert "所有者" in message


def test_metadata_loss_rejection_lists_all_blocking_categories():
    losses = LossReport(owner=True, xattr=True, flags=True)
    message = render.render_metadata_loss_rejection("e1", losses)
    assert "所有者" in message and "xattr" in message and "flags" in message


# ─── dry-run preview（C25）─────────────────────────────────────────────────


def test_dry_run_preview_always_shows_mode_preserved_and_acl_not_checked():
    losses = LossReport(owner=False, xattr=False, flags=False)
    message = render.render_dry_run_preview(losses)
    assert "mode" in message
    assert "ACL" in message


def test_dry_run_preview_lists_potential_losses():
    losses = LossReport(owner=True, xattr=True, flags=False)
    message = render.render_dry_run_preview(losses)
    assert "所有者" in message
    assert "xattr" in message


def test_dry_run_preview_notes_xattr_not_checked_when_incapable():
    losses = LossReport(owner=False, xattr=False, flags=False, xattr_not_checked=True)
    message = render.render_dry_run_preview(losses)
    assert "検査していません" in message or "検出" in message


# ─── conflict メッセージ（C8-C11）───────────────────────────────────────────


def test_conflict_message_leads_with_direction_label():
    diff = render.build_diff_summary(
        before_text="a\nb\n", current_text="a\nc\n", current_bytes=b"a\nc\n",
        before_sha="beforesha", current_sha="currentsha",
    )
    message = render.render_conflict_message("evdiff_x", diff)
    assert message.startswith("この差分は「戻した場合に失われる内容」です")
    assert "採用パッチと採用後の変更の両方を含みます" in message


def test_conflict_message_includes_required_summary_fields():
    diff = render.build_diff_summary(
        before_text="a\n", current_text="b\n", current_bytes=b"b\n",
        before_sha="beforesha", current_sha="currentsha",
    )
    message = render.render_conflict_message("evdiff_x", diff)
    assert "beforesha" in message
    assert "currentsha" in message
    assert "変更行数" in message


def test_conflict_message_binary_content_shows_placeholder_not_hunk():
    diff = render.build_diff_summary(
        before_text="secret-before-line-xyz\n", current_text=None,
        current_bytes=b"\xff\xfe\x00binary",
        before_sha="beforesha", current_sha="currentsha",
    )
    message = render.render_conflict_message("evdiff_x", diff)
    assert "binary または decode 不能" in message
    assert "secret-before-line-xyz" not in message  # before 全文は出さない


def test_conflict_message_hunk_is_line_capped():
    """C8: 全文・生 base64 を出さない（巨大出力の切断 pitfall）。行数上限付き hunk。"""
    before_text = "".join(f"line{i}\n" for i in range(200))
    current_text = "".join(f"changed{i}\n" for i in range(200))
    diff = render.build_diff_summary(
        before_text=before_text, current_text=current_text,
        current_bytes=current_text.encode("utf-8"),
        before_sha="b", current_sha="c", max_hunk_lines=10,
    )
    message = render.render_conflict_message("evdiff_x", diff)
    assert message.count("\n") < 400  # 200行×2の全文は出ていない
    assert "省略" in message


def test_conflict_message_prints_three_next_actions_with_entry_id():
    diff = render.build_diff_summary(
        before_text="a\n", current_text="b\n", current_bytes=b"b\n",
        before_sha="b", current_sha="c",
    )
    message = render.render_conflict_message("evdiff_target", diff)
    assert "evdiff_target --dump-before" in message
    assert "evdiff_target --apply" in message
    assert "1)" in message and "2)" in message and "3)" in message


# ─── apply 完了メッセージ（N1・C29）─────────────────────────────────────────


def test_apply_success_message_includes_n1_reappear_notice():
    message = render.render_apply_success()
    assert "戻しました" in message
    assert "また提案されることがあります" in message
    assert "意図した動作です" in message
