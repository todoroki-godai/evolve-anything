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


# ─── dry-run preview + diff summary（#469）────────────────────────────────


def test_dry_run_preview_without_diff_is_unchanged():
    """diff 未指定時は従来どおり3行のみ（後方互換）。"""
    losses = LossReport(owner=False, xattr=False, flags=False)
    message = render.render_dry_run_preview(losses)
    assert message.count("\n") == 1  # 「保持: mode」「ACL: ...」の2行
    assert "変更行数" not in message


def test_dry_run_preview_prepends_diff_summary_before_existing_three_lines():
    """#469: diff を渡すと「変更行数: +N / -M 行」が先頭に付き、既存3行は末尾に残る。"""
    losses = LossReport(owner=False, xattr=False, flags=False)
    diff = render.build_diff_summary(
        before_text="a\nb\n", current_text="a\nb\nc\n", current_bytes=b"a\nb\nc\n",
        before_sha="b", current_sha="c",
    )
    message = render.render_dry_run_preview(losses, diff=diff)
    lines = message.split("\n")
    assert lines[0].startswith("変更行数:")
    assert "+1" in lines[0] and "-0" in lines[0]
    # 既存3行は最後に残る
    assert "保持: mode" in message
    assert "ACL: 保持されない・検出もしていません" in message


def test_dry_run_preview_diff_binary_shows_placeholder_not_counts():
    losses = LossReport(owner=False, xattr=False, flags=False)
    diff = render.build_diff_summary(
        before_text="secret\n", current_text=None, current_bytes=b"\xff\xfe",
        before_sha="b", current_sha="c",
    )
    message = render.render_dry_run_preview(losses, diff=diff)
    assert "判定不能" in message
    assert "binary" in message


# ─── build_diff_summary の追加/削除行数（#469）─────────────────────────────


def test_build_diff_summary_reports_added_and_removed_line_counts():
    diff = render.build_diff_summary(
        before_text="a\nb\nc\n", current_text="a\nx\nc\nd\n", current_bytes=b"a\nx\nc\nd\n",
        before_sha="b", current_sha="c",
    )
    # b→x(変更=削除+追加) + d追加 → removed=1(b), added=2(x, d)
    assert diff["removed_lines"] == 1
    assert diff["added_lines"] == 2


def test_build_diff_summary_binary_has_no_added_removed_counts():
    diff = render.build_diff_summary(
        before_text="a\n", current_text=None, current_bytes=b"\xff",
        before_sha="b", current_sha="c",
    )
    assert diff["added_lines"] is None
    assert diff["removed_lines"] is None


# ─── dry-run ヘッダ（対象パス + 分岐ラベル・#469）───────────────────────────


def test_render_dry_run_header_includes_absolute_and_relative_path():
    header = render.render_dry_run_header(
        target_path="/repo/skills/x/SKILL.md", relative_path="skills/x/SKILL.md",
        branch="normal",
    )
    assert "/repo/skills/x/SKILL.md" in header
    assert "skills/x/SKILL.md" in header


def test_render_dry_run_header_branch_labels_are_distinct_for_three_branches():
    labels = {
        b: render.render_dry_run_header(target_path="/t", relative_path="t", branch=b)
        for b in ("normal", "idempotent", "conflict")
    }
    assert len({v for v in labels.values()}) == 3


def test_render_dry_run_header_omits_relative_path_when_absent():
    header = render.render_dry_run_header(target_path="/t", relative_path=None, branch="normal")
    assert "/t" in header
    assert "repo 相対パス" not in header


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
