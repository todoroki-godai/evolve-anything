"""evolve_revert._render — 利用者に見えるメッセージの生成（#402 段階3 §2 手順3 / §8 N1）。

conflict 次アクション3行・hardlink 拒否・メタデータ拒否・diff 向きラベル・apply 完了時の
N1 予告メッセージ。全て純粋関数（文字列組立のみ・I/O ゼロ）。
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, Optional

from ._metadata import LossReport

# 巨大出力の切断 pitfall（pitfall_large_json_stdout_truncation）対策: conflict 表示は
# 全文・生 base64 を出さず、行数上限付き hunk のみ（C8）。
MAX_HUNK_LINES = 40

_LOSS_LABELS = {"owner": "所有者", "xattr": "xattr", "flags": "flags"}

# §8 N1: revert された accept は effective view から消える＝accept でも reject でもない
# ため、fleet/propose.py の再提示抑制に掛からず同じパッチが再提案されうる（意図した
# 動作）。驚きの発生地点（apply 完了時）で予告する。
N1_REAPPEAR_NOTICE = (
    "戻しました。なお同じ改善が今後また提案されることがあります"
    "（意図した動作です。不要なら提示時に n で拒否してください）。"
)


def render_apply_success() -> str:
    """apply 完了メッセージ（§8 N1・C29）。"""
    return N1_REAPPEAR_NOTICE


def render_hardlink_rejection(nlink: int) -> str:
    """hardlink 拒否メッセージ（C12）。理由（整合性破壊）と回復手順を1行で書く。
    ``--allow-metadata-loss`` では解除できないことを明示する（M5・C24 の整合性破壊分類）。
    """
    return (
        f"このファイルは hardlink されています（nlink={nlink}）。置換すると他のリンク先と"
        "内容が分岐するため revert では扱えません（--allow-metadata-loss でも解除不可）。"
        "リンクを解消してから再実行してください。"
    )


def render_metadata_loss_rejection(entry_id: str, losses: LossReport) -> str:
    """メタデータ拒否メッセージ（C13）。``--allow-metadata-loss`` 付きの実コマンドを添える。"""
    lost = [_LOSS_LABELS[name] for name in ("owner", "xattr", "flags") if getattr(losses, name)]
    lines = [
        f"このまま revert すると次のメタデータが失われます: {', '.join(lost)}",
        "これを許容する場合は次のコマンドで再実行してください:",
        f"  bin/evolve-revert {entry_id} --apply --allow-metadata-loss",
    ]
    return "\n".join(lines)


def render_dry_run_preview(losses: LossReport) -> str:
    """dry-run の「何が変わる予定か」表示（C25）。保持: mode / 失う可能性: 各属性。

    ACL は常に「検出もしていない」旨を明示表示する（検出できないものを理由に拒否は
    しない・C21）。xattr が検出不能な環境でも同様に明示表示する（C19）。
    """
    lines = ["保持: mode"]
    lost = [_LOSS_LABELS[name] for name in ("owner", "xattr", "flags") if getattr(losses, name)]
    if lost:
        lines.append(f"失う可能性: {', '.join(lost)}")
    lines.append("ACL: 保持されない・検出もしていません")
    if losses.xattr_not_checked:
        lines.append("xattr: この環境には検出手段が無いため検査していません")
    return "\n".join(lines)


def build_diff_summary(
    *,
    before_text: str,
    current_text: Optional[str],
    current_bytes: bytes,
    before_sha: str,
    current_sha: str,
    max_hunk_lines: int = MAX_HUNK_LINES,
) -> Dict[str, Any]:
    """conflict 時の diff 要約データを組み立てる（C9）。

    ``current_text`` が ``None``（decode 不能・binary）の場合は hunk を出さない契約
    （``render_conflict_message`` 側で「binary または decode 不能」に切り替える）。
    """
    before_bytes_len = len(before_text.encode("utf-8"))
    current_bytes_len = len(current_bytes)
    if current_text is None:
        return {
            "before_sha": before_sha,
            "current_sha": current_sha,
            "before_bytes": before_bytes_len,
            "current_bytes": current_bytes_len,
            "changed_lines": None,
            "hunk": None,
            "hunk_truncated": False,
            "binary_or_undecodable": True,
        }

    before_lines = before_text.splitlines(keepends=True)
    current_lines = current_text.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(before_lines, current_lines, lineterm=""))
    changed_lines = sum(
        1
        for line in diff_lines
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    truncated = len(diff_lines) > max_hunk_lines
    return {
        "before_sha": before_sha,
        "current_sha": current_sha,
        "before_bytes": before_bytes_len,
        "current_bytes": current_bytes_len,
        "changed_lines": changed_lines,
        "hunk": diff_lines[:max_hunk_lines],
        "hunk_truncated": truncated,
        "binary_or_undecodable": False,
    }


def render_conflict_message(entry_id: str, diff: Dict[str, Any]) -> str:
    """conflict メッセージ（C8-C11）。先頭1行で diff の向きを明示し、末尾に次アクション
    3手順を必ず印字する（行き止まりにしない・v2 round4 tacchi [Must]）。
    """
    lines = [
        "この差分は「戻した場合に失われる内容」です"
        "（採用パッチと採用後の変更の両方を含みます）。",
        "",
        f"before_sha: {diff['before_sha']}",
        f"現在のディスクの sha: {diff['current_sha']}",
        f"before のバイト数: {diff['before_bytes']}",
        f"現在のバイト数: {diff['current_bytes']}",
    ]
    if diff["binary_or_undecodable"]:
        lines.append(f"binary または decode 不能（{diff['current_bytes']} bytes）")
    else:
        lines.append(f"変更行数: {diff['changed_lines']}")
        lines.append("")
        lines.extend(diff["hunk"] or [])
        if diff.get("hunk_truncated"):
            lines.append(f"...(以下省略。行数上限 {MAX_HUNK_LINES} 行)")
    lines.append("")
    lines.append("次アクション:")
    lines.append(
        f"  1) bin/evolve-revert {entry_id} --dump-before <path>   # 変更前の全文を取り出して確認"
    )
    lines.append("  2) 内容に納得したら、その全文で対象ファイルを置き換える")
    lines.append(
        f"  3) bin/evolve-revert {entry_id} --apply                # 履歴も整合させて revert を確定"
    )
    return "\n".join(lines)
